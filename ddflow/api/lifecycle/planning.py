"""One way to ask the scheduler what is ready: `plan_for`.

Part of `ddflow.api.lifecycle`, which re-exports the public functions and classes defined here.

`plan()` takes five optional arguments and ten call sites once filled them differently:
`next` and `wait` passed the waiters' reservation hold and the parallelism limit, `status`
and `brief` the limit only, the progress line and the doctor neither. So `status` and
`brief` could call "ready" an item `next` held back for a waiter. `plan_for` fills them
once, and a caller says WHY it asks (``purpose``); the differences left are the named ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.schedule import plan
from ...services import flowstate as FL
from .reservations import _reservation_hold

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ...config import Config
    from ...core.model import Event, State
    from ...core.schedule import Plan

#: Why a caller asks (``plan_for(purpose=...)``).
#: ``offer``: the offer an agent acts on (`next`, `wait`). ``view``: a report of that same
#: offer (`brief`, `workflow_state`; `status` follows) -- the same answer, so a report never calls
#: ready what `next` withholds. ``structure``: the queue's shape only (`doctor`'s cycles
#: and blockers), with no reservations and no parallelism limit.
PURPOSES: tuple[str, ...] = ("offer", "view", "structure")


def plan_for(
    repo: Path,
    log,
    cfg: Config,
    st: State,
    *,
    purpose: str,
    kind: str = "task",
    phase: str = "",
    agent: str = "",
    now: float | None = None,
    events: Callable[[], Sequence[Event]] | Sequence[Event] | None = None,
) -> Plan:
    """The ready set for ``purpose``, computed with the caller's identity (``agent``, else
    the configured id, else the log's), the reservation hold and the parallelism limit."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown plan purpose {purpose!r}: one of {', '.join(PURPOSES)}")
    me = agent or cfg.agent.id or log.agent_id
    if purpose == "structure":
        return plan(st, cfg, kind=kind, phase=phase, now=now, agent=me)
    return plan(
        st,
        cfg,
        kind=kind,
        phase=phase,
        now=now,
        agent=me,
        hold=_reservation_hold(repo, st, cfg, me, now),
        parallel=FL.limit_for(repo, cfg, st, log.read_all if events is None else events),
    )


def alternatives_offer(repo: Path, log, cfg: Config):
    """What a refused claim names as "you could take instead": the first five of the offer
    `next` makes now, in its order, for the items of the refused item's kind, minus the
    refused one. Handed to
    `services.leases.acquire(offer=...)`, which cannot compute it (it needs the repository
    for the waiters' reservations and the load). Computed only when a claim is refused."""

    def offer(state: State, item_id: str, holder: str, now: float) -> list[str]:
        refused = state.items.get(item_id)
        p = plan_for(
            repo,
            log,
            cfg,
            state,
            purpose="offer",
            kind=refused.kind if refused else "task",
            agent=holder,
            now=now,
        )
        return [it.id for it in p.ready if it.id != item_id][:5]  # in `next`'s own order

    return offer
