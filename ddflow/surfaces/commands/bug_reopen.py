"""`ddflow bug reopen` -- the human surface for `api.bug_reopen` (B7bdcc6b212)."""

from __future__ import annotations

import sys

from ...api.bug_reopen import bug_reopen
from ...core.outcome import OK
from ..context import Ctx


def cmd_bug_reopen(a, c: Ctx) -> int:
    out = bug_reopen(c.repo, a.id, reason=a.reason or "", agent=c.requested_agent)
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    c.out(
        f"bug {a.id} reopened (was {out.data['was']}): {out.data['reason_given']}\n{out.data['next']}",
        out.body(
            ("id", "was", "reason_given", "fix_task", "fix_task_state", "previous_fix_task", "next")
        ),
    )
    return OK
