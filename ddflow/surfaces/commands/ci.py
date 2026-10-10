"""`ddflow ci ...` -- the human surface for `api.ci`."""

from __future__ import annotations

import sys

from ...api import ci as A
from ...api import surf_setup as SS
from ..context import Ctx
from ..render import emit_json


def cmd_ci(a, c: Ctx) -> int:
    if a.verb == "record":
        if not a.result:
            print("ci record needs --result passed|failed", file=sys.stderr)
            return 1
        out = A.record(c.repo, stage=a.stage, ok=a.result == "passed", sha=a.sha, report=a.report)
        if c.json:
            emit_json(out.body(""))
        elif out.exit:
            print(out.reason, file=sys.stderr)
        else:
            print(f"ci: recorded {a.stage} {out.data['status']}")
        return out.exit
    if a.verb == "status":
        out = A.status(c.repo)
        if c.json:
            emit_json(out.body(""))
            return out.exit
        d = out.data
        print(f"command:   {d['command'] or '(none)'}")
        print(f"base:      {d['base']}")
        print(f"timeout:   {d['timeout_s']}s")
        print(f"available: {'yes' if d['available'] else 'NO -- ' + d['why']}")
        return out.exit
    # The default is the HEAD of the tree the command runs in: as a gate it runs in the
    # item's worktree, while c.repo is the primary checkout (whose HEAD is main).
    ref = a.ref or SS.head_here_or_repo(c.repo)
    out = A.run(c.repo, ref=ref, base=a.base, command=a.command)
    if c.json:
        emit_json(out.body(""))
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
