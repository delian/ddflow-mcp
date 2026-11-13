"""Runtime lane-budget admission: interrupt a lane once its usage reaches its template budget.

``launch`` admits a lane on an ESTIMATED cost (its ``_constraints`` compile ``budget_usd`` against
``score.est_cost``), but nothing stops a lane that loops or runs away AFTER it launches, so a
runaway spends unbounded. This module is the runtime half: the lane supervisor sums the ``usage``
events the shipped adapter already emits (``opencode.STREAM_RULES`` reads ``part.cost``) and
interrupts the lane ONCE at ``budget_usd``, with a soft warn at a ``budget_warn_fraction`` of it.
A lane whose usage cannot be read is reported cap-unenforced -- never silently uncapped
(unavailable-not-passed, the lane-adapter port). The loop mirrors ``swarm.conformance.observe``
(the one other reader of a live lane's events) but its only job is the budget stop.

Decisions in force: D-swarm-answers (``budget_usd`` is the cap a person approved), D-sched-no-daemon
(the supervisor is the operator's foreground process, not a daemon: it calls this), r-bounded-everything
(one warn, one interrupt, one record, no re-entries), r-adapters (``interrupt`` is the adapter's
capability, called when advertised, never imitated; absent, the contract's ``stop`` is the graceful
signal after ``stop_grace_s``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: Verdict outcomes.
STOPPED = "stopped"
UNDER = "under_budget"
CAP_UNENFORCED = "cap_unenforced"

#: ``Record`` kind names: the vocabulary this module asks the caller to write. The caller owns
#: the log (and each kind's registration); this module owns the names and the payload shape, so
#: the enforcement cannot shop the stop outcome out to several ad-hoc event kinds.
RECORD_WARNED = "lane.budget_warned"
RECORD_STOPPED = "lane.budget_stopped"
RECORD_UNENFORCED = "lane.cap_unenforced"

#: ``record(kind, data) -> None``. ``data`` always carries ``lane`` and ``cap``; the stop and
#: unenforced records add ``spent`` and ``source``.
Record = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class Verdict:
    """What one enforcement pass saw. ``interrupts`` is 0 (not stopped, or stopped via the
    contract's ``stop``) or 1 (the adapter's ``interrupt`` was called once)."""

    outcome: str  #: stopped | under_budget | cap_unenforced
    spent: float  #: USD summed from the usage events seen
    cap: float  #: the ``budget_usd`` enforced
    warned: bool = False
    interrupts: int = 0
    reason: str = ""


@dataclass(frozen=True)
class Params:
    """The runtime knobs, read once from ``[swarm]`` (mirrors ``conformance.Params``)."""

    enabled: bool = True
    warn_fraction: float = 0.5
    stop_grace_s: float = 10.0

    @classmethod
    def from_config(cls, cfg) -> Params:
        sw = cfg.swarm
        return cls(
            enabled=bool(sw.budget_stop_enabled),
            warn_fraction=float(sw.budget_warn_fraction),
            stop_grace_s=float(sw.budget_stop_grace_s),
        )


def _usage_cost(ev: Any) -> float | None:
    """The USD cost of one ``usage`` event, or ``None`` when it cannot be read."""
    fields = getattr(ev, "fields", None)
    cost = fields.get("cost") if isinstance(fields, Mapping) else None
    if cost is None:
        return None
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(cost) else cost


def _lane_name(handle: Any) -> str:
    for attr in ("lane", "name"):
        v = getattr(handle, attr, None)
        if isinstance(v, str) and v:
            return v
    spec = getattr(handle, "spec", None)
    name = getattr(spec, "name", "")
    return name if isinstance(name, str) else ""


def _interrupt(driver: Any, handle: Any, stop_grace_s: float) -> int:
    """Send the stop signal once: the adapter's ``interrupt`` when it advertises the capability
    and has the method, else the contract's ``stop`` with the grace. Returns interrupt count."""
    caps = getattr(handle, "capabilities", frozenset())
    fn = getattr(driver, "interrupt", None)
    if "interrupt" in caps and callable(fn):
        fn(handle)
        return 1
    driver.stop(handle, stop_grace_s)
    return 0


def _unenforced(
    record, lane: str, cap: float, spent: float, source: str, reason: str, warned: bool = False
) -> Verdict:
    """Record ``lane.cap_unenforced`` and return the verdict (never silently uncapped)."""
    if record is not None:
        record(
            RECORD_UNENFORCED,
            {"lane": lane, "cap": cap, "spent": spent, "source": source, "reason": reason},
        )
    return Verdict(CAP_UNENFORCED, spent, cap, warned=warned, reason=reason)


def _warn(record, lane: str, cap: float, spent: float, fraction: float, source: str) -> None:
    if record is not None:
        record(
            RECORD_WARNED,
            {"lane": lane, "cap": cap, "spent": spent, "fraction": fraction, "source": source},
        )


def enforce(
    driver: Any,
    handle: Any,
    *,
    budget_usd: float,
    params: Params | None = None,
    source: str = "lane-budget",
    record: Record | None = None,
) -> Verdict:
    """Sum the lane's ``usage`` events and interrupt it once at ``budget_usd``.

    ``driver`` and ``handle`` come from a started lane (``driver.start``); ``params`` are the
    ``[swarm]`` runtime knobs (``Params.from_config``): ``warn_fraction`` (the soft warn) and
    ``stop_grace_s`` for the graceful stop, and ``enabled`` turns the stop off. ``record``, when
    given, is told about the soft warn (``lane.budget_warned``), the stop (``lane.budget_stopped``
    with ``spent``, ``cap``, ``source``) or an unenforceable cap (``lane.cap_unenforced``). The
    ``cost`` read off each ``usage`` event is the adapter's CANONICAL field, ``part.cost`` as the
    transport's stream rules map it (``opencode.STREAM_RULES``). A lane that reports no ``usage``
    capability, or a ``usage`` event whose cost cannot be read, is cap-unenforced, never silently
    uncapped."""
    p = params or Params()
    warn_fraction = float(p.warn_fraction)
    stop_grace_s = float(p.stop_grace_s)
    cap = float(budget_usd)
    lane = _lane_name(handle)
    if not p.enabled:
        return Verdict(UNDER, 0.0, cap, reason="the runtime budget stop is disabled")
    if cap <= 0:
        return Verdict(UNDER, 0.0, cap, reason="no budget to enforce")
    caps = getattr(handle, "capabilities", frozenset())
    if "usage" not in caps:
        return _unenforced(record, lane, cap, 0.0, source, "the lane reports no usage capability")
    spent = 0.0
    warned = False
    seen = 0
    for ev in driver.events(handle):
        if ev.event != "usage":
            continue
        seen += 1
        step = _usage_cost(ev)
        if step is None:
            return _unenforced(
                record, lane, cap, spent, source, "a usage event carried no cost", warned=warned
            )
        spent += step
        if not warned and spent >= cap * float(warn_fraction):
            warned = True
            _warn(record, lane, cap, spent, warn_fraction, source)
        if spent >= cap:
            interrupts = _interrupt(driver, handle, stop_grace_s)
            if record is not None:
                record(
                    RECORD_STOPPED,
                    {"lane": lane, "cap": cap, "spent": spent, "source": source},
                )
            return Verdict(STOPPED, spent, cap, warned=True, interrupts=interrupts)
    if seen == 0:
        return _unenforced(record, lane, cap, 0.0, source, "no usage event was seen")
    return Verdict(UNDER, spent, cap, warned=warned)
