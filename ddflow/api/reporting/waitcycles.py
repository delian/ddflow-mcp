"""The lease wait-for cycles `doctor` reports, read from a folded state."""

from __future__ import annotations

import time

from ...core import health as H
from ...core import leasewait as LW
from ...services import autoorder as AOS
from ...services import healthwatch as HW
from ...services import waiting as WT


def wait_cycles(st, cfg, now: float | None = None) -> list[LW.Cycle]:
    """Cycles in the wait-for graph of the standing `lease.waiting` records (empty when
    `[loops].wait_cycle` is off)."""
    if not cfg.loops.wait_cycle:
        return []
    now = time.time() if now is None else now
    waits = [
        LW.Wait(
            w.item, w.holder, w.since, tuple(w.globs), tuple((o["item"], o["holder"]) for o in w.on)
        )
        for w in WT.waiters(st, cfg, now)
    ]
    live = st.active_leases(now, cfg.lease.grace_s)
    held = [LW.Held(i, lz.holder, tuple(lz.globs)) for i, lz in live.items()]
    return LW.find(waits, held)


def wait_findings(cycles: list[LW.Cycle], cfg) -> list[str]:
    """The cycles as `doctor` problems: the first `[loops].wait_cycle_shown` in full."""
    shown = cfg.loops.wait_cycle_shown
    out = [c.render() for c in cycles[:shown]]
    if shown and len(cycles) > shown:
        out.append(f"{len(cycles) - shown} more lease deadlock(s)")
    return out


def wait_order(log, st, cfg, cycles: list[LW.Cycle], notes: list[str], repo=None) -> dict:
    """What `[schedule].auto_order` does about ``cycles`` (DX-conflict-autoorder): its
    lines join ``notes``, with the watchdog's (``repo``); the result is the `doctor` data, with
    the edges standing now."""
    ordered = AOS.run(log, st, cfg, [c.as_dict() for c in cycles])
    notes += AOS.lines(ordered)
    if repo is not None:
        notes += health_lines(log, st, cfg, repo, cycles)
    return {**ordered, "standing": AOS.auto_edges(log.read_all(), st)}


def health_lines(log, st, cfg, repo, cycles, now: float | None = None, activity=None) -> list[str]:
    """Finding lines not already shown elsewhere, at most `[health].shown` in full."""
    if all(m == "off" for m in HW.modes(cfg).values()):
        return []
    at = time.time() if now is None else now
    found = [
        f
        for f in HW.inspect(log, st, cfg, repo, at, cycles, activity=activity)
        if f.kind != H.CRASHED and f.evidence.get("via") != "cycle"
    ]
    shown = cfg.health.shown
    out = [f"health: {f.render()}" for f in found[:shown]]
    if shown and len(found) > shown:
        out.append(f"health: {len(found) - shown} more finding(s) (`ddflow health`)")
    return out
