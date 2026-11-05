"""The parallelism-cap wording: `parallel_line` and the cap helpers."""

from __future__ import annotations

from ...config import Config
from .. import flowcontrol as FC
from ..flowparams import params

#: `Decision.limited_by` when the adaptive limit could not be derived at all
#: (`services/flowstate.limit_for`): the line says so, with the reason, and nothing
#: overrides it.
LIMIT_UNAVAILABLE = "unavailable"


def _words(signal: str) -> str:
    """A controller's ``limited_by`` as people read it: ``load_per_core`` -> "load per
    core"; the controller's own phrases ("ceiling", "independent work") pass through."""
    return signal.replace("_", " ")


def _cap_words(cfg: Config, parallel: FC.Decision | None, limit: int) -> str:
    """What the parallelism cap is, for `cap_note` and "cap reached". Without a Decision,
    exactly the words it always had (`_blocking_leases` keys on "cap reached"); in auto,
    the adaptive limit and what limits it; in fixed, the fixed knob when it holds, or the
    lowered limit and the signal that lowered it."""
    if parallel is None:
        return f"schedule.max_parallel_tasks={cfg.schedule.max_parallel_tasks}"
    if parallel.mode == "fixed":
        if parallel.admit_paused:
            return f"fixed: admission paused -- {parallel.reason}"
        if limit < cfg.schedule.max_parallel_tasks:
            return f"fixed: limit {limit}, limited by {_words(parallel.limited_by)}"
        return f"schedule.max_parallel_tasks={cfg.schedule.max_parallel_tasks}"
    if parallel.admit_paused:
        return f"auto: admission paused -- {parallel.reason}"
    return f"auto: limit {limit}, limited by {_words(parallel.limited_by)}"


def parallel_line(
    cfg: Config,
    parallel: FC.Decision,
    limit: int,
    in_flight: int,
    offered: int,
    capped: list[str],
) -> str:
    """The one line `status` and `brief` print: "parallel: 4 (fixed)", "parallel: 6
    (auto: ceiling 8; limited by <what>)", or, in fixed mode with the limit lowered
    under load, "parallel: 3 (fixed: ceiling 4; limited by load per core)". When nothing
    is held by the cap -- the offer already holds every ready item that can run beside
    what is in flight -- what limits the work is the work itself: "limited by independent
    work"."""
    if parallel.mode == "fixed":
        if parallel.admit_paused:
            return f"parallel: {limit} (fixed: admission paused -- {parallel.reason})"
        if limit < cfg.schedule.max_parallel_tasks:
            by = _words(parallel.limited_by)
            return (
                f"parallel: {limit} (fixed: ceiling "
                f"{cfg.schedule.max_parallel_tasks}; limited by {by})"
            )
        return f"parallel: {limit} (fixed)"
    if parallel.limited_by == LIMIT_UNAVAILABLE:
        return (
            f"parallel: {limit} (auto: unavailable, holding the start value -- {parallel.reason})"
        )
    ceiling = params(cfg).bounds()[2]
    by = parallel.limited_by
    if parallel.admit_paused:
        by = f"{by}: admission paused"
    elif not capped and in_flight + offered <= limit:
        by = FC.INDEPENDENT
    return f"parallel: {limit} (auto: ceiling {ceiling}; limited by {_words(by)})"
