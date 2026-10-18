"""Claim guard: an item that is already looping (`[loops].on_detect = "block"`)."""

from __future__ import annotations

from ...core import outcome as O
from ...core import progress as PR
from .claim_guards import ClaimContext, register


@register("looping", order=10)
def refuse_looping(ctx: ClaimContext) -> O.Outcome | None:
    """The refusal for an item that is already looping; ``--force`` passes it."""
    item = ctx.item
    looping = [
        f
        for f in PR.detect(ctx.events, ctx.st, ctx.cfg)
        if f.item == item and f.severity == "block"
    ]
    if not looping or ctx.force:
        return None
    return O.refused(
        "item.claimed",
        f"refusing to claim {item}: it is already looping.\n"
        + "\n".join(f"  {f.render()}" for f in looping)
        + "\n\nRe-claiming it would continue the loop. Change the task, abandon it, "
        "or --force if you have fixed the underlying cause.",
        id=item,
        looping=[f.__dict__ for f in looping],
    )
