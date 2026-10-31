#!/usr/bin/env python3
"""Interim transcript token report (TE-measure): the aggregates of docs/ddflow/research/token-spend.md s3.

Usage: python3 -I scripts/token_report.py PROJECT_DIR [--since DATE] [--until DATE] [--json]

Read-only over Claude Code transcripts (*.jsonl under PROJECT_DIR; files under a `subagents` dir are
subagents, the rest the lead). Prints aggregates only: counts, token sums, weighted units, shares and
file stems. No transcript text is ever printed or stored. An unreadable file is listed under
UNAVAILABLE, never counted as zero; exit 2 when nothing could be read.

INTERIM: this parser is replaced by B-am-ingest (the single transcript reader, TE-Q5) when it lands;
do not import it from ddflow/. Every tunable below is a knob (module constant or CLI flag)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Knobs. Weighted units: Anthropic's published price ratios (research s2).
WEIGHTS = {"input": 1.0, "read": 0.1, "write_5m": 1.25, "write_1h": 2.0, "output": 5.0}
GAP_S = 300  # a gap at least this long before a call can cost a cache rewrite (5 m TTL)
MIN_WRITE = 50_000  # a cache write at least this big after such a gap is a "rewrite"
BYTES_PER_TOKEN = 4  # content size estimate, research s2
TOP_AGENTS = 10
# First match wins; matched against "<tool name> <command>" of the last tool call before the gap.
TOOL_CLASSES = [
    ("wait", r"ddflow[ _-]wait"),
    ("review", r"ddflow[ _-]review|roborev"),
    ("gate", r"gate[ _-]run|ddflow_gate|pytest|unit_tests|\bci\b"),
    ("poll", r"\bsleep\b|claim|heartbeat|Monitor"),
]
OTHER = "other"
SCHEMA = "token_report@1"


class Unavailable(Exception):
    pass


def classify_tool(name: str, command: str, classes=None) -> str:
    text = f"{name} {command}"
    for cls, pat in classes or TOOL_CLASSES:
        if re.search(pat, text):
            return cls
    return OTHER


def _time(entry):
    try:
        return datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def _size(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text.encode("utf-8", "replace"))


def read_entries(path: Path):
    """Yield JSON objects of a transcript; UNAVAILABLE if unreadable or nothing parses."""
    good = bad = 0
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    good += 1
                    out.append(obj)
    except OSError as exc:
        raise Unavailable(f"{path.stem}: {type(exc).__name__}") from exc
    if not good:
        raise Unavailable(f"{path.stem}: no parseable entries")
    return out


def _usage(u: dict) -> dict:
    cc = u.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
    return {
        "input": u.get("input_tokens") or 0,
        "read": u.get("cache_read_input_tokens") or 0,
        "write_5m": w5 or 0,
        "write_1h": w1 or 0,
        "output": u.get("output_tokens") or 0,
    }


def _content_class(entry, blocks, tool_names):
    """Yield (class, bytes) for the content an entry adds to the context."""
    if entry.get("type") == "assistant":
        for b in blocks:
            t = b.get("type")
            if t == "text":
                yield "lead_text", _size(b.get("text", ""))
            elif t == "tool_use":
                n = b.get("name", "")
                cls = {
                    "Bash": "bash_command",
                    "Agent": "launch_brief",
                    "Task": "launch_brief",
                    "SendMessage": "sendmessage",
                    "AskUserQuestion": "askuser",
                }.get(n, "tool_input_other")
                yield cls, _size(b.get("input", ""))
        return
    for b in blocks:
        if b.get("type") == "tool_result":
            n = tool_names.get(b.get("tool_use_id"), "")
            yield (
                ("bash_result" if n == "Bash" else "tool_result_other"),
                _size(b.get("content", "")),
            )
        else:
            text = b.get("text", "") if isinstance(b, dict) else ""
            if "<task-notification" in text:
                cls = "subagent_report"
            elif "<system-reminder" in text:
                cls = "system_reminder"
            else:
                cls = "operator"
            yield cls, _size(text)


def _blocks(msg: dict) -> list:
    raw = msg.get("content")
    if isinstance(raw, str):
        return [{"type": "text", "text": raw}]
    return [b for b in raw or [] if isinstance(b, dict)]


def _note_call(calls, order, mid, msg, ts, tool_names, classes):
    c = calls.get(mid)
    if c is None:
        c = calls[mid] = {"first": ts, "last": ts, "tools": []}
        order.append(mid)
    c["last"] = ts or c["last"]
    c["usage"] = _usage(msg["usage"])
    for b in _blocks(msg):
        if b.get("type") == "tool_use":
            tool_names[b.get("id")] = b.get("name", "")
            inp = b.get("input")
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            c["tools"].append(classify_tool(b.get("name", ""), str(cmd), classes))


def _first_seen(seen: set, mid, block: dict) -> bool:
    """True once per (message id, block): a re-streamed entry must not add its content twice."""
    if not mid:
        return True
    key = (mid, json.dumps(block, sort_keys=True, ensure_ascii=False))
    if key in seen:
        return False
    seen.add(key)
    return True


def analyse_file(path: Path, since=None, until=None, classes=None) -> dict:
    """Per-file calls (one per message id, last streamed usage) and content adds."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    adds: list[tuple[str, int, int]] = []  # class, bytes, calls seen when added
    tool_names: dict[str, str] = {}
    seen: set = set()
    for e in read_entries(path):
        day = str(e.get("timestamp", ""))[:10]
        if (since and day < since) or (until and day > until):
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        if e.get("type") == "assistant" and msg.get("id") and isinstance(msg.get("usage"), dict):
            _note_call(calls, order, msg["id"], msg, _time(e), tool_names, classes)
        if e.get("type") in ("assistant", "user"):
            fresh = [b for b in _blocks(msg) if _first_seen(seen, msg.get("id"), b)]
            for cls, size in _content_class(e, fresh, tool_names):
                adds.append((cls, size, len(order)))
    return {"calls": [calls[m] for m in order], "adds": adds}


def _empty():
    return {
        "calls": 0,
        "input": 0,
        "read": 0,
        "write_5m": 0,
        "write_1h": 0,
        "output": 0,
        "units": 0.0,
    }


def _units(u: dict, w: dict) -> float:
    return sum(u[k] * w[k] for k in w)


def _fold_calls(calls, agg, rewrites, w, gap_s, min_write) -> float:
    """Add a file's calls into agg; record cache rewrites (long gap, big write) by preceding tool."""
    units = 0.0
    prev = None
    for c in calls:
        u = c["usage"]
        agg["calls"] += 1
        for k in ("input", "read", "write_5m", "write_1h", "output"):
            agg[k] += u[k]
        units += _units(u, w)
        if prev is not None and c["first"] and prev["last"]:
            gap = (c["first"] - prev["last"]).total_seconds()
            wr = u["write_5m"] + u["write_1h"]
            if gap >= gap_s and wr >= min_write:
                cls = prev["tools"][-1] if prev["tools"] else OTHER
                r = rewrites.setdefault(
                    cls, {"events": 0, "tokens": 0, "units": 0.0, "wait_s": 0.0}
                )
                r["events"] += 1
                r["tokens"] += wr
                r["units"] += u["write_5m"] * w["write_5m"] + u["write_1h"] * w["write_1h"]
                r["wait_s"] += gap
        prev = c
    return units


def _finish_rewrites(rewrites, rep, w, classes):
    for cls in {c for c, _ in classes or TOOL_CLASSES} | {OTHER}:
        rewrites.setdefault(cls, {"events": 0, "tokens": 0, "units": 0.0, "wait_s": 0.0})
    su = rep["subagents"]["units"]
    for r in rewrites.values():
        wait_s = r.pop("wait_s")
        r["avg_wait_min"] = round(wait_s / r["events"] / 60, 2) if r["events"] else 0.0
        r["share_of_subagent_units"] = r["units"] / su if su else 0.0


def _finish_carry(carry):
    tot_added = sum(d["added"] for d in carry.values())
    tot_carry = sum(d["carry"] for d in carry.values())
    for d in carry.values():
        d["share_added"] = d["added"] / tot_added if tot_added else 0.0
        d["share_carry"] = d["carry"] / tot_carry if tot_carry else 0.0


def build_report(
    root,
    since=None,
    until=None,
    weights=None,
    gap_s=GAP_S,
    min_write=MIN_WRITE,
    classes=None,
    top=TOP_AGENTS,
) -> dict:
    root = Path(root)
    w = weights or WEIGHTS
    files = sorted(root.rglob("*.jsonl")) if root.is_dir() else []
    rep = {
        "schema": SCHEMA,
        "root": root.name,
        "since": since,
        "until": until,
        "weights": w,
        "gap_s": gap_s,
        "min_write": min_write,
        "unavailable": [],
        "lead": {**_empty(), "status": "OK", "files": 0},
        "subagents": {**_empty(), "status": "OK", "agents": 0},
    }
    if not files:
        rep["unavailable"].append(f"{root.name}: no transcript files")
    rewrites: dict[str, dict] = {}
    carry: dict[str, dict] = {}
    per_agent = []
    failed = {"lead": 0, "subagents": 0}
    for p in files:
        sub = "subagents" in p.parts[len(root.parts) :]
        try:
            res = analyse_file(p, since, until, classes)
        except Unavailable as exc:
            rep["unavailable"].append(str(exc))
            failed["subagents" if sub else "lead"] += 1
            continue
        agg = rep["subagents" if sub else "lead"]
        agg["agents" if sub else "files"] += 1
        file_units = _fold_calls(res["calls"], agg, rewrites if sub else {}, w, gap_s, min_write)
        agg["units"] += file_units
        if sub:
            per_agent.append({"agent": p.stem, "calls": len(res["calls"]), "units": file_units})
        else:
            n = len(res["calls"])
            for cls, size, seen in res["adds"]:
                d = carry.setdefault(cls, {"added": 0, "carry": 0})
                tok = size // BYTES_PER_TOKEN
                d["added"] += tok
                d["carry"] += tok * max(n - seen, 0)
    for side, n in failed.items():
        ok = rep[side]["files" if side == "lead" else "agents"]
        if n:
            rep[side]["status"] = "PARTIAL" if ok else "UNAVAILABLE"
    total = rep["lead"]["units"] + rep["subagents"]["units"]
    rep["total_units"] = total
    rep["share"] = {k: (rep[k]["units"] / total if total else 0.0) for k in ("lead", "subagents")}
    rep["rewrite_tokens"] = sum(r["tokens"] for r in rewrites.values())
    _finish_rewrites(rewrites, rep, w, classes)
    rep["rewrites"] = rewrites
    _finish_carry(carry)
    rep["carry"] = carry
    rep["top_agents"] = sorted(per_agent, key=lambda a: -a["units"])[:top]
    return rep


def render_text(rep: dict) -> str:
    L = [
        f"token report ({rep['schema']}) {rep['root']} window {rep['since'] or '-'}..{rep['until'] or '-'}"
    ]
    for k in ("lead", "subagents"):
        a = rep[k]
        L.append(
            f"{k:10} {a['status']:11} calls {a['calls']:>7} read {a['read']:>13,} w5m {a['write_5m']:>12,}"
            f" w1h {a['write_1h']:>11,} out {a['output']:>10,} units {a['units']:>15,.0f}"
            f" share {rep['share'][k]:.1%}"
        )
    L.append(
        f"rewrites (gap >= {rep['gap_s']} s, write >= {rep['min_write']:,}) by preceding tool:"
    )
    for cls, r in sorted(rep["rewrites"].items(), key=lambda kv: -kv[1]["tokens"]):
        L.append(
            f"  {cls:8} events {r['events']:>5} tokens {r['tokens']:>13,} avg wait {r['avg_wait_min']:>6} min"
            f" share of subagent units {r['share_of_subagent_units']:.1%}"
        )
    L.append("carry by content class (lead):")
    for cls, d in sorted(rep["carry"].items(), key=lambda kv: -kv[1]["carry"]):
        L.append(
            f"  {cls:18} added {d['added']:>9,} ({d['share_added']:.1%}) carry share {d['share_carry']:.1%}"
        )
    L.append(
        "top agents by units: "
        + ", ".join(f"{a['agent']}={a['units']:.0f}" for a in rep["top_agents"])
    )
    for u in rep["unavailable"]:
        L.append(f"UNAVAILABLE {u}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--since", help="first day included, YYYY-MM-DD")
    ap.add_argument("--until", help="last day included, YYYY-MM-DD")
    ap.add_argument("--gap-s", type=int, default=GAP_S)
    ap.add_argument("--min-write", type=int, default=MIN_WRITE)
    ap.add_argument("--top", type=int, default=TOP_AGENTS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = build_report(a.root, a.since, a.until, gap_s=a.gap_s, min_write=a.min_write, top=a.top)
    print(json.dumps(rep, indent=1) if a.json else render_text(rep))
    nothing = rep["lead"]["calls"] + rep["subagents"]["calls"] == 0 and rep["unavailable"]
    return 2 if nothing else 0


if __name__ == "__main__":
    sys.exit(main())
