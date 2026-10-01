"""Regenerate the duplicate-detection evaluation corpus from ddflow's own event log.

    python tests/fixtures/dedupe/build_fixture.py [--events .ddflow/events] [--check]

Writes ``corpus.jsonl`` beside this file: one line per bug, task and phase ever added to
the log (removed and closed ones included -- a duplicate is often filed against a record
that is closed by now), with its latest title and body, its state and when it was added.
``pairs.jsonl`` is NOT generated: it is the hand-verified label set (decision
D-no-duplicates, research R-dedupe-matchers), edited by hand with a reason per pair.
``--check`` rebuilds in memory and fails if a labelled id is missing from the corpus.

Every text is redacted on the way out, deterministically, so the committed fixture names
nobody's own services (decision D-no-own-services-local-dir): secrets by the session
redaction patterns ddflow ships, private-network addresses, versioned model names (a
private deployment's served names), this machine's host name and home directory. The
redactor is a fixed point -- ``redact(redact(x)) == redact(x)`` -- and the test checks the
committed corpus is one, so a hand edit that reintroduces an address fails.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from ddflow.config import Config  # noqa: E402
from ddflow.services.sessions import redact as redact_secrets  # noqa: E402

CORPUS = HERE / "corpus.jsonl"
PAIRS = HERE / "pairs.jsonl"

LAN = "<lan-address>"
MODEL = "<served-model>"
HOST = "<host>"

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:])")
#: A versioned model name, with or without its org: `org/Name-4.1-x`, `Name3.8-27B`. The
#: family word alone ("the LAN Qwen") is generic and stays; the served name goes.
MODEL_NAME = re.compile(
    r"(?i)\b(?:[a-z0-9][\w-]*/)?"
    r"(?:gemma|qwen|deepseek|gemini|llama|mistral|mixtral|gpt|kimi|glm|phi)"
    r"[\w.-]*?\d[\w.-]*\w"
)
HOME = re.compile(r"/home/(?!user/)[^/\s'\"`]+/")


def _private(raw: str, kind: type) -> bool:
    try:
        ip = kind(raw)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified or (ip.version == 4 and ip.packed[0] == 0):
        return False
    return ip.is_private or ip.is_link_local


def redact(text: str, *, host: str | None = None) -> str:
    """The fixture's redaction: deterministic, idempotent, and the same on every machine
    except for the host name it is told (default: this machine's)."""
    out, _ = redact_secrets(text, Config())
    for rx, kind in ((_IPV4, ipaddress.IPv4Address), (_IPV6, ipaddress.IPv6Address)):
        out = rx.sub(lambda m, k=kind: LAN if _private(m.group(1), k) else m.group(0), out)
    out = MODEL_NAME.sub(MODEL, out)
    host = socket.gethostname().split(".")[0] if host is None else host
    if len(host) >= 3:
        out = re.sub(rf"(?i)\b{re.escape(host)}\b", HOST, out)
    return HOME.sub("/home/user/", out)


def _events(events_dir: Path) -> list[dict]:
    out = []
    for f in sorted(events_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    out.sort(key=lambda e: (e["lamport"], e["agent"], e["id"]))
    return out


def build(events_dir: Path) -> list[dict]:
    recs: dict[str, dict] = {}
    for e in _events(events_dir):
        k, s, d = e["kind"], e["subject"], e.get("data") or {}
        if k in ("task.added", "phase.added"):
            recs[s] = {
                "id": s,
                "kind": k.split(".")[0],
                "title": d.get("title", ""),
                "body": d.get("body", ""),
                "state": "open",
                "added": e["ts"],
            }
        elif k in ("task.updated", "phase.updated") and s in recs:
            recs[s].update({f: d[f] for f in ("title", "body") if f in d})
        elif k == "item.started" and s in recs:
            recs[s]["state"] = "claimed"
        elif k == "item.completed" and s in recs:
            recs[s]["state"] = "done"
        elif k in ("task.removed", "item.abandoned") and s in recs:
            recs[s]["state"] = "removed"
        elif k == "bug.found":
            r = recs.setdefault(
                s,
                {
                    "id": s,
                    "kind": "bug",
                    "title": "",
                    "body": "",
                    "state": "open",
                    "added": e["ts"],
                },
            )
            r["body"] = d.get("summary", "") or r["body"]
        elif k == "bug.fixed" and s in recs:
            recs[s]["state"] = "fixed"
        elif k == "bug.invalid" and s in recs and recs[s]["state"] != "fixed":
            recs[s]["state"] = "invalid"
    out = []
    for r in recs.values():
        if not (r["title"] or r["body"]):
            continue
        r["title"], r["body"] = redact(r["title"]), redact(r["body"])
        out.append(r)
    out.sort(key=lambda r: (r["added"], r["id"]))
    return out


def labelled_ids() -> set[str]:
    ids = set()
    for line in PAIRS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            p = json.loads(line)
            ids.update((p["a"], p["b"]))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--events", type=Path, default=ROOT / ".ddflow" / "events")
    ap.add_argument("--check", action="store_true", help="rebuild in memory, write nothing")
    a = ap.parse_args()
    recs = build(a.events)
    missing = sorted(labelled_ids() - {r["id"] for r in recs})
    if missing:
        print(f"labelled ids missing from the corpus: {missing}", file=sys.stderr)
        return 1
    if not a.check:
        with CORPUS.open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{len(recs)} records{' (checked)' if a.check else f' -> {CORPUS}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
