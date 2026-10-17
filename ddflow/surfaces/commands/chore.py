"""`chore commit` and `chore approve` -- the human surface for `api.chore` (D-chore-class)."""

from __future__ import annotations

import sys

from ...api import chore as A
from ..context import OK, Ctx

COMMIT_PAYLOAD = ("item", "sha", "files", "skipped_gates", "approved_by", "why", "stage")
APPROVE_PAYLOAD = ("digest", "globs", "approved_by")


def _finish(c: Ctx, out, payload: tuple[str, ...]) -> int:
    if out.exit == OK:
        c.out(out.data["text"], out.body(payload))
    elif c.json:
        c.out(out.reason, {**out.body(payload), "reason": out.reason})
    else:
        print(out.reason, file=sys.stderr)
    return out.exit


def cmd_chore_commit(a, c: Ctx) -> int:
    out = A.chore_commit(
        c.repo,
        list(a.files or []),
        message=a.message or "",
        model=a.model or "",
        agent=c.requested_agent,
        called_from=c.called_from,
    )
    return _finish(c, out, COMMIT_PAYLOAD)


def cmd_chore_approve(a, c: Ctx) -> int:
    out = A.chore_approve(c.repo, agent=c.requested_agent, note=a.note or "")
    return _finish(c, out, APPROVE_PAYLOAD)
