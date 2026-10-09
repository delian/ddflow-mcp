"""`ddflow bisect`: find the earlier test file that makes another test fail (B26)."""

from __future__ import annotations

import sys

from ...api import bisect as A
from ..context import FAIL, Ctx
from ..render import emit_json


def cmd_bisect(a, c: Ctx) -> int:
    out = A.bisect(
        c.repo,
        a.victim,
        a.cmd,
        candidates=a.candidates,
        glob=a.glob,
        timeout_s=a.timeout,
        repeat=a.repeat,
        max_runs=a.max_runs,
    )
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        emit_json(out.body(("state", "victim", "polluters", "candidates", "summary", "runs")))
        return out.exit
    d = out.data
    lines = [f"victim: {d['victim']}", f"candidates before it: {d['candidates']}", ""]
    if d["state"] == "found":
        lines.append("Run before the victim, these make it fail (remove any one and it passes):")
        lines += [f"  {p}" for p in d["polluters"]]
    else:
        lines.append(f"Nothing to report ({d['state']}): {d['summary']}")
        if d["polluters"]:
            lines.append("Smallest set that still fails so far:")
            lines += [f"  {p}" for p in d["polluters"]]
    lines += ["", f"{len(d['runs'])} run(s)"]
    for r in d["runs"]:
        note = f" -- {r['detail']}" if r["detail"] and r["outcome"] != "passed" else ""
        lines.append(f"  {r['outcome']:<13s} {r['tests']:>4d} test(s)  {r['seconds']}s{note}")
    print("\n".join(lines))
    return out.exit
