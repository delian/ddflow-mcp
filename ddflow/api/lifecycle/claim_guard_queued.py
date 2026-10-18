"""Claim guard: files an earlier QUEUED waiter asked for first ([lease].wait_queue)."""

from __future__ import annotations

import time

from ...core import outcome as O
from ...services import waiting as WG
from ...services.claimguards import ClaimContext, register


def _refuse_if_queued(ctx: ClaimContext) -> O.Outcome | None:
    """The refusal for a claim of files an earlier QUEUED waiter asked for first."""
    it = ctx.st.items.get(ctx.item)
    if ctx.skip or it is None:
        return None
    want = ctx.want
    ahead = WG.queue_ahead(
        ctx.st, ctx.cfg, ctx.item, ctx.me, list(want if want is not None else it.globs), time.time()
    )
    if ahead is None:
        return None
    return O.refused(
        "item.claimed",
        f"{ctx.item}: those files are queued behind {ahead.holder} ({ahead.item}), who asked "
        f"for them first ([lease].wait_queue).",
        id=ctx.item,
        alternatives=[],
        queued_behind=ahead.item,
    )


register("queued", _refuse_if_queued, order=20)
