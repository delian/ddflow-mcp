"""The lease wait-for cycles and wait alarms `doctor` and `status` report, from a folded state."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Any

from ...adapters.clock import DEFAULT as CLOCK
from ...core import clock
from ...core import health as H
from ...core import leasewait as LW
from ...services import autoorder as AOS
from ...services import healthwatch as HW
from ...services import jobs as JOBS
from ...services import lanes as LN
from ...services import leasebreak as LB
from ...services import waiting as WT
from ...services.gates import running as RUN

#: `(item, lease) -> epoch seconds of the newest work on it`
Progress = Callable[[str, Any], float]


def progress_of(log, st, cfg, now: float | None = None, events=None, activity=None) -> Progress:
    """The `Progress` of this state, reading nothing until first asked: a queue with no
    stuck wait pays no git or log read. Work in flight is progress as of now: a live gate
    run, or a running job within `[health].runaway_s` (past it the job is a RUNAWAY)."""
    at = CLOCK.wall() if now is None else now
    act = activity or LB.tree_activity(log.root)
    budget = cfg.health.runaway_s

    @cache
    def facts() -> tuple[Any, set[str]]:
        running = (
            j
            for j in st.jobs.values()
            if not j.ended_at and (budget <= 0 or at - clock.epoch(j.started_at) < budget)
        )
        busy = {j.item for j in running if JOBS.status(j).state == "running"}
        return (log.read_all() if events is None else events), busy

    measured: dict[str, float] = {}

    def progress(item: str, lease) -> float:
        if item not in measured:
            seen, busy = facts()
            live = item in busy or RUN.live_runs(log.root, item)
            measured[item] = at if live else LB.progress_at(seen, item, lease, act)
        return measured[item]

    return progress


def _standing(st, cfg, now: float) -> tuple[list[LW.Wait], dict[str, Any]]:
    """The standing waits, as `leasewait` reads them, and the live leases by item."""
    waits = [
        LW.Wait(
            w.item, w.holder, w.since, tuple(w.globs), tuple((o["item"], o["holder"]) for o in w.on)
        )
        for w in WT.waiters(st, cfg, now)
    ]
    return waits, st.active_leases(now, cfg.lease.grace_s)


def _held(live: dict[str, Any], measure: set[str], progress: Progress | None) -> list[LW.Held]:
    """The live leases, with the watermark of those in ``measure`` when ``progress`` is given."""
    return [
        LW.Held(
            i, lz.holder, tuple(lz.globs), progress(i, lz) if progress and i in measure else 0.0
        )
        for i, lz in live.items()
    ]


def wait_cycles(
    st, cfg, now: float | None = None, progress: Progress | None = None
) -> list[LW.Cycle]:
    """Cycles in the wait-for graph of the standing `lease.waiting` records (empty when
    `[loops].wait_cycle` is off). With ``progress`` the victim is the holder with the least
    of it; without, the youngest wait."""
    if not cfg.loops.wait_cycle:
        return []
    now = CLOCK.wall() if now is None else now
    waits, live = _standing(st, cfg, now)
    cycles = LW.find(waits, _held(live, set(), None))
    if not cycles or progress is None:
        return cycles
    ring = {b for c in cycles for k in c.links for b in k.blockers}
    return LW.find(waits, _held(live, ring, progress))


def wait_alarms(
    st, cfg, cycles: list[LW.Cycle], progress: Progress, now: float | None = None
) -> list[LW.Alarm]:
    """Waits older than `[loops].wait_alarm_s` with nothing running on the leases in their
    way; only the leases of waits that old are measured."""
    limit = cfg.loops.wait_alarm_s
    if limit <= 0:
        return []
    now = CLOCK.wall() if now is None else now
    waits, live = _standing(st, cfg, now)
    aged = [w for w in waits if now - w.since >= limit]
    way = {b for w in aged for b, _h in w.on}
    return LW.alarms(aged, _held(live, way, progress), cycles, now, limit)


def wait_report(
    log, st, cfg, events=None, now: float | None = None, activity=None
) -> tuple[list, list]:
    """``(cycles, alarms)`` as of ``now``: the one answer `doctor`, `status`, `brief` and the
    lanes advisor read."""
    now = CLOCK.wall() if now is None else now
    progress = progress_of(log, st, cfg, now, events, activity)
    cycles = wait_cycles(st, cfg, now, progress)
    return cycles, wait_alarms(st, cfg, cycles, progress, now)


def wait_rows(cycles: list[LW.Cycle], alarms: list[LW.Alarm]) -> dict[str, list[dict[str, Any]]]:
    """The structured report for a wire body: a key only when it has rows."""
    rows = {
        "wait_cycles": [c.as_dict() for c in cycles],
        "wait_alarms": [a.as_dict() for a in alarms],
    }
    return {k: v for k, v in rows.items() if v}


def _runnable(drops: tuple[LW.Drop, ...]) -> str:
    """The line to run for ``drops``, or `doctor` when `safe` would change it: the quotes it
    strips are what keep a glob from the shell, and `doctor` prints the commands in full."""
    line = "; ".join(d.command() for d in drops)
    return line if line and LN.safe(line) == line else "ddflow doctor"


def _deadlock_move(c: LW.Cycle) -> LN.Move:
    loop = " -> ".join(LN.safe(a) for a in (*c.agents, c.agents[0]))
    gives = ", ".join(LN.safe(g) for g in c.release) or "nothing it holds, and reports it"
    return LN.Move(
        "deadlock",
        f"lease deadlock {loop}: {LN.safe(c.victim)} yields {gives}",
        tuple(LN.safe(a) for a in c.agents),
        _runnable(c.drops),
    )


def _alarm_move(a: LW.Alarm) -> LN.Move:
    way = ", ".join(f"{LN.safe(i)} ({LN.safe(h)})" for i, h, _s in a.on)
    return LN.Move(
        "wait-alarm",
        f"{LN.safe(a.agent)} on {LN.safe(a.item)} has waited {clock.fmt_age(a.age_s)} and "
        f"nothing runs on {way}",
        (LN.safe(a.agent),),
        _runnable(a.drops),
    )


def wait_moves(moves: list, cycles: list[LW.Cycle], alarms: list[LW.Alarm], cap: int) -> list:
    """The advisor's ``moves`` with the stuck waits behind lost work and ahead of the rest
    (`services.lanes.advise.KINDS`' order), at most ``cap``."""
    stuck = [*map(_deadlock_move, cycles), *map(_alarm_move, alarms)]
    resume = [m for m in moves if m.kind == "resume"]
    rest = [m for m in moves if m.kind != "resume"]
    return [*resume, *stuck, *rest][: max(0, cap)]


def _shown(rows, shown: int, noun: str) -> list[str]:
    more = len(rows) - shown
    return [r.render() for r in rows[:shown]] + (
        [f"{more} more {noun}"] if shown and more > 0 else []
    )


def wait_findings(cycles: list[LW.Cycle], cfg, alarms: list[LW.Alarm] | None = None) -> list[str]:
    """The cycles and alarms as problems: the first `[loops].wait_cycle_shown` of each in full."""
    shown = cfg.loops.wait_cycle_shown
    return [
        *_shown(cycles, shown, "lease deadlock(s)"),
        *_shown(alarms or [], shown, "lease wait alarm(s)"),
    ]


def wait_order(log, st, cfg, cycles: list[LW.Cycle], notes: list[str], repo=None) -> dict:
    """What `[schedule].auto_order` does about ``cycles`` (DX-conflict-autoorder): its
    lines join ``notes``, with the watchdog's (``repo``); the result is the `doctor` data, with
    the edges standing now."""
    ordered = AOS.run(log, st, cfg, [c.as_dict() for c in cycles])
    notes += AOS.lines(ordered)
    if repo is not None:
        notes += health_lines(log, st, cfg, repo, cycles)
    return {**ordered, "standing": AOS.auto_edges(log.read_all(), st)}


def health_lines(
    log, st, cfg, repo, cycles, now: float | None = None, activity=None, recoveries=None
) -> list[str]:
    """Finding lines not already shown elsewhere, at most `[health].shown` in full."""
    if not HW.enabled(HW.modes(cfg)):
        return []
    at = CLOCK.wall() if now is None else now
    by_kind = HW.modes(cfg)
    found = [
        f
        for f in HW.inspect(
            log, st, cfg, repo, at, cycles, activity=activity, recoveries=recoveries
        )
        # a lapsed lease is shown by `recover`'s notes; a live lease whose process is gone is not
        if by_kind[f.kind] != "off"
        and (f.kind != H.CRASHED or f.evidence.get("pids"))
        and f.evidence.get("via") != "cycle"
    ]
    shown = cfg.health.shown
    out = [f"health: {f.render()}" for f in found[:shown]]
    if shown and len(found) > shown:
        out.append(f"health: {len(found) - shown} more finding(s) (`ddflow health`)")
    return out
