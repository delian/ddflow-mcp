"""Claim guard: files that are held for a waiter in line."""

from __future__ import annotations

import time

from ...core import outcome as O
from ...services.claimguards import ClaimContext, register
from .claim_line import _join_line
from .reservations import _reserved_for, _reserved_msg


def _refuse_if_reserved(ctx: ClaimContext) -> O.Outcome | None:
    """The refusal for a claim of files that are held for a waiter in line; None if free."""
    it = ctx.st.items.get(ctx.item)
    if ctx.skip or it is None:
        return None
    cfg, item, me, want = ctx.cfg, ctx.item, ctx.me, ctx.want
    now = time.time()
    live = ctx.st.active_leases(now, cfg.lease.grace_s)
    ahead = _reserved_for(
        ctx.repo, ctx.st, cfg, it, me, list(want if want is not None else it.globs), live, now
    )
    if ahead is None:
        return None
    reason = _reserved_msg(item, ahead, cfg)
    _join_line(ctx.repo, cfg, me, item, [ahead.item], reason)
    return O.refused(
        "item.claimed",
        reason + f"\n\n`ddflow wait --item {item}` sleeps until it is yours; your place in "
        f"line is kept while you wait or keep asking.",
        id=item,
        alternatives=[],
        reserved_for=ahead.agent,
    )


register("reserved", _refuse_if_reserved, order=30)
