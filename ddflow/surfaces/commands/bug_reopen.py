"""`ddflow bug reopen` -- the human surface for `api.bug_reopen` (B7bdcc6b212)."""

from __future__ import annotations

import sys

from ...api.bug_reopen import bug_reopen
from ...core.outcome import OK
from ..context import Ctx


def add_bug_reopen_parser(bug_sub) -> None:
    br = bug_sub.add_parser(
        "reopen", help="reopen a bug closed by mistake (fixed or invalid), saying why"
    )
    br.add_argument("id")
    br.add_argument("--reason", required=True, help="why the closure was wrong")
    br.set_defaults(fn=cmd_bug_reopen)


def cmd_bug_reopen(a, c: Ctx) -> int:
    out = bug_reopen(c.repo, a.id, reason=a.reason or "", agent=c.requested_agent)
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    task, state = out.data["fix_task"], out.data["fix_task_state"]
    if not task:
        tail = "\nno task was filed to fix it: `ddflow bug file-tasks` files one"
    elif state == "done":
        tail = f"\nits fix task {task} is done: `ddflow verify {task} --reopen` reopens it"
    else:
        tail = f"\nfix task: {task}"
    c.out(
        f"bug {a.id} reopened (was {out.data['was']}): {out.data['reason_given']}{tail}",
        out.body(("id", "was", "reason_given", "fix_task", "fix_task_state", "previous_fix_task")),
    )
    return OK
