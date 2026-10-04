"""`ddflow ci ...` -- the human surface for `api.ci`."""

from __future__ import annotations

import json
import sys

from ...api import ci as A
from ..context import Ctx


def add_ci_parser(sub) -> None:
    ci = sub.add_parser(
        "ci", help="the CI parity gate: run the pre-push checks on the merge result"
    )
    ci_s = ci.add_subparsers(dest="ci_cmd", required=False)
    run = ci_s.add_parser(
        "run", help="run the checks in a scratch worktree of the branch merged with the base"
    )
    run.add_argument("--ref", default="HEAD", help="the commit to check (default HEAD)")
    run.add_argument(
        "--base",
        default="",
        help="merge this branch in first (default [ci].base or the default branch)",
    )
    run.add_argument("--command", default="", help="run this instead of [ci].command")
    run.set_defaults(fn=cmd_ci)
    st = ci_s.add_parser("status", help="what `ci run` would execute here, and whether it can")
    st.set_defaults(fn=cmd_ci)
    ci.set_defaults(fn=cmd_ci, ci_cmd="status")


def cmd_ci(a, c: Ctx) -> int:
    if (a.ci_cmd or "status") == "status":
        out = A.status(c.repo)
        if c.json:
            print(json.dumps(out.body(""), indent=2, default=str))
            return out.exit
        d = out.data
        print(f"command:   {d['command'] or '(none)'}")
        print(f"base:      {d['base']}")
        print(f"timeout:   {d['timeout_s']}s")
        print(f"available: {'yes' if d['available'] else 'NO -- ' + d['why']}")
        return out.exit
    out = A.run(c.repo, ref=a.ref, base=a.base, command=a.command)
    if c.json:
        print(json.dumps(out.body(""), indent=2, default=str))
        return out.exit
    d = out.data
    if d.get("command"):
        merged = f" merged with {d['merged_with']}" if d.get("merged_with") else ""
        print(f"ci: {d['command']}  on {d.get('sha', '')[:10]}{merged}")
    for ch in d.get("checks", []):
        print(
            f"  [{'ok  ' if ch['ok'] else 'FAIL'}] {ch['id']}"
            + (f"  {ch['detail']}" if ch["detail"] else "")
        )
    if out.exit == 0:
        print("ci: passed")
        return 0
    print(out.reason, file=sys.stderr)
    if out.exit == 1 and d.get("output_tail"):
        print(d["output_tail"], file=sys.stderr)
    return out.exit
