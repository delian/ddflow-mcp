"""One way to ask the scheduler what is ready: `plan_for`.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here.

`plan()` takes five optional arguments and ten call sites once filled them differently:
`next` and `wait` passed the waiters' reservation hold and the parallelism limit, `status`
and `brief` the limit only, the progress line and the doctor neither. So `status` and
`brief` could call "ready" an item `next` held back for a waiter. `plan_for` fills them
once, and a caller says WHY it asks (``purpose``); the differences left are the named ones.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from ...config import Config
from ...core.model import Event, State
from ...core.schedule import Plan, plan
from .reservations import _reservation_hold

#: ``offer``: the offer an agent acts on (`next`, `wait`). ``view``: a report of that same
#: offer (`status`, `brief`, `workflow_state`) -- the same answer, so a report never calls
#: ready what `next` withholds. ``structure``: the queue's shape only (`doctor`'s cycles
#: and blockers), with no reservations and no parallelism limit.
Purpose = Literal["offer", "view", "structure"]
PURPOSES: tuple[str, ...] = ("offer", "view", "structure")


def plan_for(
    repo: Path,
    log,
    cfg: Config,
    st: State,
    *,
    purpose: Purpose,
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
    from ...services.flowstate import limit_for

    return plan(
        st,
        cfg,
        kind=kind,
        phase=phase,
        now=now,
        agent=me,
        hold=_reservation_hold(repo, st, cfg, me, now),
        parallel=limit_for(repo, cfg, st, log.read_all if events is None else events),
    )
