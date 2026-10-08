"""`ddflow bisect`: find the earlier test file that makes another test fail (B26)."""

from __future__ import annotations

import sys

from ...api import bisect as A
from ..context import FAIL, Ctx
from ..render import emit_json


def add_bisect_parser(s) -> None:
    b = s.add_parser(
        "bisect",
        help="find which earlier test file makes a test fail only in full-suite order "
        "(exit 0 = found, 2 = nothing to report)",
    )
    b.add_argument("victim", help="the test that fails only after others ran (a test id)")
    b.add_argument(
        "--cmd",
        required=True,
        help="a command that runs a list of tests and exits non-zero on failure, with "
        "{tests} where the list goes, e.g. 'pytest -q {tests}'",
    )
    b.add_argument("--candidates", default="", help="comma-separated files, in run order")
    b.add_argument(
        "--glob", default="", help=f"where candidates come from (default {A.DEFAULT_GLOB})"
    )
    b.add_argument("--timeout", type=float, default=600, help="seconds per run (default 600)")
    b.add_argument("--repeat", type=int, default=1, help="runs per probe; any failure counts")
    b.add_argument("--max-runs", type=int, default=200, help="stop after this many runs")
    b.set_defaults(fn=cmd_bisect)


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
