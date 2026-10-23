"""`grant add` and `grant list` -- the human surface for `api.grants`."""

from __future__ import annotations

import sys

from ...api import grants as A
from ..context import OK, Ctx


def _finish(c: Ctx, out, key: str) -> int:
    if out.exit == OK:
        c.out(out.data["text"], out.body((key,)))
    elif c.json:
        c.out(out.reason, {**out.body((key,)), "reason": out.reason})
    else:
        print(out.reason, file=sys.stderr)
    return out.exit


def cmd_grant_add(a, c: Ctx) -> int:
    out = A.grant_add(
        c.repo, item=a.item, gate=a.gate, kind=a.kind or "review_round", count=int(a.count),
        reason=a.reason or "", when=a.when or "", agent=c.requested_agent,
        approval=a.approval or "",
    )  # fmt: skip
    return _finish(c, out, "grant")


def cmd_grant_list(a, c: Ctx) -> int:
    return _finish(c, A.grant_list(c.repo, item=a.item or "", agent=c.requested_agent), "grants")
