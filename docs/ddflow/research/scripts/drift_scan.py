#!/usr/bin/env python3
"""drift_scan: read-only script/encoding drift scanner (stdlib only).

First detector of docs/ddflow/research/hallucination-drift.md (HAL-baseline-scan).
It never writes, never prints scanned text: findings are counts plus codepoint
samples (U+XXXX, script) so a transcript scan cannot leak content.

Run isolated:  python3 -I drift_scan.py MODE [options]

MODE
  files        tracked files of a git repo (git ls-files), classified by file type
  commits      commit messages (git log)
  events       .ddflow/events/*.jsonl shards (decoded JSON string values)
  transcripts  Claude Code *.jsonl transcripts: per context-fill bucket aggregates

Categories (per scanned text):
  foreign_letters  letters whose script is not in --allow-script (default: Latin)
  mixed_word       one word mixing Latin with another script (homoglyph risk)
  zero_width       U+200B..200F, 2060..2064, FEFF (not at file start)
  bidi             U+202A..202E, 2066..2069
  replacement      U+FFFD
  mojibake         UTF-8 read as Latin-1/cp1252 (Ã©, â€™, Ð¿ ...)
  control          C0/C1 controls other than \\t \\n \\r
  private_use      Co/Cn codepoints
  nonascii_symbol  non-ASCII non-letters (arrows, box drawing, dashes): NOT drift,
                   counted to show what a naive "non-ASCII" detector would flag
"""
from __future__ import annotations

import argparse
import collections
import fnmatch
import functools
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import unicodedata

ZW = set(range(0x200B, 0x2010)) | set(range(0x2060, 0x2065)) | {0xFEFF}
BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
MOJI = re.compile(
    "(?:[ÂÃ][\u0080-¿¡-¿]"        # Ã© Â°
    "|â€[™œ“”¦–—˜]"  # â€™ â€œ
    "|[ÐÑ][\u0080-¿¡-¿]"          # Ð¿ Ñ  (Cyrillic)
    "|ï»¿)"                                  # BOM
)
WORD = re.compile(r"\w+", re.UNICODE)


@functools.lru_cache(maxsize=None)
def script_of(ch: str) -> str:
    """Script of a letter: first word of its Unicode name, CJK folded."""
    try:
        n = unicodedata.name(ch)
    except ValueError:
        return "UNNAMED"
    w = n.split()[0]
    if w == "MICRO":
        return "LATIN"  # U+00B5 micro sign: a unit symbol, not a script
    if w in ("CJK", "IDEOGRAPHIC"):
        return "CJK"
    if w == "FULLWIDTH":
        return "LATIN" if "LATIN" in n else "FULLWIDTH"
    return w


def scan_text(text: str, allow=frozenset({"LATIN"})) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    if text.isascii():
        for ch in text:
            o = ord(ch)
            if o < 32 and ch not in "\t\n\r":
                c["control"] += 1
        return c
    scripts_seen = collections.Counter()
    for i, ch in enumerate(text):
        o = ord(ch)
        if o < 128:
            if o < 32 and ch not in "\t\n\r":
                c["control"] += 1
            continue
        if o in ZW:
            if not (o == 0xFEFF and i == 0):
                c["zero_width"] += 1
            continue
        if o in BIDI:
            c["bidi"] += 1
            continue
        if o == 0xFFFD:
            c["replacement"] += 1
            continue
        cat = unicodedata.category(ch)
        if cat[0] == "L":
            sc = script_of(ch)
            if sc not in allow:
                c["foreign_letters"] += 1
                c["script:" + sc] += 1
        elif cat in ("Co", "Cn"):
            c["private_use"] += 1
        elif cat in ("Cc",):
            c["control"] += 1
        elif cat[0] in "MC":
            pass
        else:
            c["nonascii_symbol"] += 1
    if MOJI.search(text):
        c["mojibake"] += len(MOJI.findall(text))
    # mixed-script words (Latin + other): only words that have both
    for w in WORD.findall(text):
        if w.isascii():
            continue
        s = {script_of(ch) for ch in w if ch.isalpha()}
        if "LATIN" in s and len(s) > 1:  # accented Latin is still one script
            c["mixed_word"] += 1
    return c


def pieces_py(src: str):
    """Yield (kind, text) for a Python source: comment, string, code."""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                yield "comment", tok.string
            elif tok.type == tokenize.STRING:
                yield "string", tok.string
            elif tok.type in (tokenize.NAME, tokenize.OP):
                yield "code", tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        yield "text", src


def kind_of_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return "py"
    if ext in (".md", ".rst", ".txt"):
        return "doc"
    if ext in (".json", ".jsonl", ".toml", ".yaml", ".yml", ".cfg", ".ini"):
        return "data"
    return "other"


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, check=False)


class Agg:
    def __init__(self):
        self.total = collections.Counter()
        self.by_kind = collections.defaultdict(collections.Counter)
        self.files = collections.defaultdict(collections.Counter)  # path -> counter
        self.samples = collections.Counter()  # (codepoint, script) -> n

    def add(self, key, path, c, text=None, nsamples=0):
        if not c:
            return
        self.total.update(c)
        self.by_kind[key].update(c)
        self.files[path].update(c)


def sample_codepoints(text, allow, store, limit=4000):
    n = 0
    for ch in text:
        if ord(ch) > 127 and (ch.isalpha() and script_of(ch) not in allow):
            store[(f"U+{ord(ch):04X}", script_of(ch))] += 1
            n += 1
            if n > limit:
                break
        elif ord(ch) in ZW or ord(ch) in BIDI or ord(ch) == 0xFFFD:
            store[(f"U+{ord(ch):04X}", "FORMAT")] += 1


def mode_files(a, allow):
    out = git(a.root, "ls-files", "-z").stdout.decode("utf-8", "replace").split("\0")
    ag = Agg()
    n = nb = 0
    for p in out:
        if not p:
            continue
        fp = os.path.join(a.root, p)
        try:
            raw = open(fp, "rb").read()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            nb += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            ag.add("undecodable", p, collections.Counter(not_utf8=1))
            continue
        n += 1
        k = kind_of_path(p)
        if k == "py":
            for pk, t in pieces_py(text):
                c = scan_text(t, allow)
                ag.add("py:" + pk, p, c)
                if c.get("foreign_letters") or c.get("mixed_word"):
                    sample_codepoints(t, allow, ag.samples)
        else:
            c = scan_text(text, allow)
            ag.add(k, p, c)
            if c.get("foreign_letters") or c.get("mixed_word"):
                sample_codepoints(text, allow, ag.samples)
    return ag, {"text_files": n, "binary_skipped": nb}


def mode_commits(a, allow):
    raw = git(a.root, "log", "--format=%H%x01%an%x01%B%x02", f"-n{a.limit}").stdout.decode("utf-8", "replace")
    ag = Agg()
    n = 0
    for rec in raw.split("\x02"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, _an, body = (rec.split("\x01") + ["", ""])[:3]
        n += 1
        ag.add("commit", h[:10], scan_text(body, allow))
        if scan_text(body, allow).get("foreign_letters"):
            sample_codepoints(body, allow, ag.samples)
    return ag, {"commits": n}


def walk_json_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from walk_json_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_json_strings(v)


def mode_events(a, allow):
    d = os.path.join(a.root, ".ddflow", "events")
    ag = Agg()
    n = bad = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        for line in open(os.path.join(d, fn), encoding="utf-8", errors="replace"):
            try:
                obj = json.loads(line)
            except ValueError:
                bad += 1
                continue
            n += 1
            t = obj.get("type") or obj.get("kind") or "?"
            for s in walk_json_strings(obj):
                c = scan_text(s, allow)
                if c:
                    ag.add("event:" + str(t), fn, c)
                    if c.get("foreign_letters"):
                        sample_codepoints(s, allow, ag.samples)
    return ag, {"events": n, "unparsable_lines": bad}


BUCKET = 50_000


def mode_transcripts(a, allow):
    """Aggregate by (model, context-fill bucket). Never prints content."""
    root = a.transcripts
    stats = collections.defaultdict(collections.Counter)
    sessions_hit = collections.Counter()
    nfiles = 0
    for dp, _dn, fns in os.walk(root):
        if a.project and a.project not in dp:
            continue
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            nfiles += 1
            fp = os.path.join(dp, fn)
            bucket_of_call = {}
            edited = set()
            user_foreign = False
            sess_stats = collections.defaultdict(collections.Counter)
            seen_req = set()
            try:
                fh = open(fp, encoding="utf-8", errors="replace")
            except OSError:
                continue
            last_ctx = 0
            last_model = "?"
            for line in fh:
                if '"type":"assistant"' not in line and '"type":"user"' not in line and '"type": "assistant"' not in line and '"type": "user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                m = d.get("message") or {}
                if t == "assistant":
                    u = m.get("usage") or {}
                    ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                    if ctx <= 0:
                        ctx = last_ctx
                    last_ctx = ctx
                    model = m.get("model") or last_model
                    last_model = model
                    key = (model, min(ctx // BUCKET, 20) * BUCKET)
                    rid = d.get("requestId") or m.get("id")
                    if rid and rid not in seen_req:
                        seen_req.add(rid)
                        stats[key]["api_calls"] += 1
                    for b in m.get("content") or []:
                        bt = b.get("type")
                        if bt == "text":
                            c = scan_text(b.get("text") or "", allow)
                            stats[key]["text_chars"] += len(b.get("text") or "")
                            for k2 in ("foreign_letters", "mixed_word", "replacement", "zero_width", "bidi", "mojibake"):
                                stats[key][k2] += c.get(k2, 0)
                            if c.get("foreign_letters") and not user_foreign:
                                sess_stats[key]["foreign_no_user_basis"] += 1
                        elif bt == "tool_use":
                            stats[key]["tool_use"] += 1
                            bucket_of_call[b.get("id")] = key
                            inp = b.get("input") or {}
                            for s in walk_json_strings(inp):
                                c = scan_text(s, allow)
                                for k2 in ("foreign_letters", "mixed_word", "replacement", "zero_width", "bidi", "mojibake"):
                                    stats[key]["tool_in_" + k2] += c.get(k2, 0)
                            if b.get("name") in ("Edit", "Write", "MultiEdit"):
                                fpth = inp.get("file_path")
                                stats[key]["edits"] += 1
                                if fpth in edited:
                                    stats[key]["re_edits"] += 1
                                edited.add(fpth)
                elif t == "user":
                    content = m.get("content")
                    if isinstance(content, str):
                        if scan_text(content, allow).get("foreign_letters"):
                            user_foreign = True
                    elif isinstance(content, list):
                        for b in content:
                            if b.get("type") == "tool_result":
                                key = bucket_of_call.get(b.get("tool_use_id"))
                                if key:
                                    stats[key]["tool_results"] += 1
                                    if b.get("is_error"):
                                        stats[key]["tool_errors"] += 1
                            elif b.get("type") == "text":
                                if scan_text(b.get("text") or "", allow).get("foreign_letters"):
                                    user_foreign = True
            for k, v in sess_stats.items():
                if v["foreign_no_user_basis"]:
                    sessions_hit[k] += 1
    rows = []
    for (model, b), c in sorted(stats.items()):
        rows.append({"model": model, "ctx_from_k": b // 1000, **dict(c), "sessions_with_foreign_no_user_basis": sessions_hit[(model, b)]})
    return rows, {"transcript_files": nfiles}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["files", "commits", "events", "transcripts"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--transcripts", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--project", default="", help="substring of the transcript dir")
    ap.add_argument("--allow-script", action="append", default=[], help="extra allowed script, e.g. CYRILLIC")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    allow = frozenset({"LATIN", *[s.upper() for s in a.allow_script]})
    if a.mode == "transcripts":
        rows, meta = mode_transcripts(a, allow)
        print(json.dumps({"meta": meta, "rows": rows}, indent=None if a.json else 1))
        return 0
    ag, meta = {"files": mode_files, "commits": mode_commits, "events": mode_events}[a.mode](a, allow)
    res = {
        "mode": a.mode,
        "meta": meta,
        "total": dict(ag.total),
        "by_kind": {k: dict(v) for k, v in sorted(ag.by_kind.items())},
        "top_paths_by_foreign_or_mixed": sorted(
            ((p, dict(c)) for p, c in ag.files.items() if c.get("foreign_letters") or c.get("mixed_word") or c.get("bidi") or c.get("zero_width") or c.get("replacement") or c.get("mojibake")),
            key=lambda x: -(x[1].get("foreign_letters", 0) + 10 * x[1].get("bidi", 0) + 10 * x[1].get("zero_width", 0) + 10 * x[1].get("replacement", 0) + x[1].get("mojibake", 0)),
        )[: a.top],
        "files_with_foreign_letters": sum(1 for c in ag.files.values() if c.get("foreign_letters")),
        "files_with_any_finding": sum(1 for c in ag.files.values() if any(k in c for k in ("foreign_letters", "mixed_word", "bidi", "zero_width", "replacement", "mojibake", "control", "private_use"))),
        "files_with_nonascii_symbol_only": sum(1 for c in ag.files.values() if c.get("nonascii_symbol") and not any(k in c for k in ("foreign_letters", "mixed_word", "bidi", "zero_width", "replacement", "mojibake"))),
        "codepoint_samples": [[k[0], k[1], n] for k, n in ag.samples.most_common(25)],
    }
    print(json.dumps(res, indent=None if a.json else 1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
