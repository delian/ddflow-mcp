"""The watchdog's facts and its corrections (DX-health-watch).

`gather` reads the folded state, the log and the worktrees into the plain values
`core.health.inspect` judges. `correct` turns findings into the moves `[health].mode` allows:

* ``off``      nothing;
* ``propose``  one line per move it WOULD make, nothing written;
* ``apply``    only moves that lose nothing, each an event that already exists:
  - a STALLED lease another item waits for, past `[lease].hold_max_s`, is broken exactly as
    `claim --break` breaks it (`lease.expired` naming who and why; the holder's branch,
    worktree and gates are kept);
  - a deadlock gets its `needs` edges from `services.autoorder` (`task.updated`, auto_order);
  - a CRASHED lane with work has its lease recorded expired (`lease.expired`), the state
    `ddflow recover` and any claim pick up.
  A RUNAWAY job is only reported. Nothing here signals a process, removes a worktree, a branch
  or a commit, or releases a lane that is making progress.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import health as H
from ..core import leasewait as LW
from ..infra.log import EventLog
from . import autoorder as AOS
from . import jobs as JOBS
from . import leasebreak as LB
from . import leases as L
from . import waiting as WT
from .schedule import last_run_epoch

ACTOR = "ddflow:health"
CADENCE = "health_watch"
MODES = ("off", "propose", "apply")


def limits(cfg) -> H.Limits:
    return H.Limits(cfg.health.stall_s, cfg.health.runaway_s)


def _newest_events(events, items: set[str]) -> dict[str, float]:
    """Item -> time of its newest event that is not lease bookkeeping (one pass)."""
    out: dict[str, float] = {}
    for e in events:
        if e.subject in items and e.kind not in LB.BOOKKEEPING:
            t = clock.epoch(e.ts)
            if t > out.get(e.subject, 0.0):
                out[e.subject] = t
    return out


def gather(
    log: EventLog,
    st,
    cfg,
    repo: Path,
    now: float,
    events=None,
    activity: LB.Activity | None = None,
) -> tuple[list[H.Lane], list[H.Proc], list[H.Wait]]:
    """The lanes (one per lease of an unfinished item), unended jobs and standing waits."""
    events = log.read_all() if events is None else events
    act = activity or LB.tree_activity(repo)
    held = {
        i: it
        for i, it in st.items.items()
        if it.lease is not None and not it.removed and not it.terminal
    }
    newest = _newest_events(events, set(held))
    recs = {
        r.item: r for r in L.scan(log, cfg, repo, now=now, state=st) if r.kind == "expired_lease"
    }
    lanes = []
    for i, it in held.items():
        lz = it.lease
        live = lz.live(now, cfg.lease.grace_s)
        if live:
            at = max(lz.acquired_at, act(lz), newest.get(i, 0.0))
            work: bool | None = False
        else:
            at, rec = lz.renewed_at, recs.get(i)
            work = None if rec is None else rec.salvageable
        recorded = not lz.live(lz.renewed_at)  # an expiry is recorded, whatever the clock says
        lanes.append(H.Lane(i, lz.holder, live, at, work, recorded, lz.worktree))
    procs = [
        H.Proc(j.id, j.item, clock.epoch(j.started_at), JOBS.status(j).state == "running")
        for j in st.jobs.values()
        if not j.ended_at
    ]
    waits = [
        H.Wait(w.item, w.holder, tuple((o["item"], o["holder"]) for o in w.on))
        for w in WT.waiters(st, cfg, now)
    ]
    return lanes, procs, waits


def inspect(log, st, cfg, repo, now, cycles: list[LW.Cycle], events=None, activity=None):
    """The findings now, deduplicated by (kind, item, holder)."""
    lanes, procs, waits = gather(log, st, cfg, repo, now, events, activity)
    found = H.inspect(lanes, procs, waits, [c.as_dict() for c in cycles], now, limits(cfg))
    return list({f.key: f for f in found}.values())


def _move(action: str, item: str, applied: bool, line: str) -> dict[str, Any]:
    return {"action": action, "item": item, "applied": applied, "line": line}


def _break(log, st, cfg, f: H.Finding, events, now, activity, mode) -> dict[str, Any] | None:
    lease = st.items[f.item].lease
    asks = WT.waiting_for(st, cfg, f.item, now)
    if not asks:
        return None
    claimant = asks[0]["item"]
    v = LB.judge(cfg, events, f.item, lease, [a["item"] for a in asks], now, activity)
    if v is None or not v.breakable:
        return _move(
            "break",
            f.item,
            False,
            f"{f.item} is stalled but not yet breakable ([lease].hold_max_s {cfg.lease.hold_max_s}s)",
        )
    text = f"break {f.item} ({lease.holder}) for {claimant}: {v.line()}"
    if mode != "apply":
        return _move("break", f.item, False, f"would {text}")
    pair = asks[0]["globs"] or list(lease.globs)
    log.append("lease.expired", f.item, LB.breaking_record(lease, claimant, ACTOR, pair, v))
    L._remote_drop(log, f.item, lease.holder)
    return _move(
        "break",
        f.item,
        True,
        f"broke {f.item} for {claimant}: work kept in {lease.worktree or 'its branch'}",
    )


def correct(
    log: EventLog,
    st,
    cfg,
    findings: list[H.Finding],
    cycles: list[LW.Cycle],
    mode: str,
    now: float,
    events=None,
    activity: LB.Activity | None = None,
) -> list[dict[str, Any]]:
    """The moves ``mode`` allows for ``findings``; in ``apply`` they are written to the log."""
    if mode not in MODES or mode == "off":
        return []
    events = log.read_all() if events is None else events
    act = activity or LB.tree_activity(log.root)
    moves: list[dict[str, Any]] = []
    for f in findings:
        if f.kind == H.STALLED and (m := _break(log, st, cfg, f, events, now, act, mode)):
            moves.append(m)
        elif f.kind == H.CRASHED and f.evidence.get("work") is True and not f.evidence["recorded"]:
            if mode == "apply":
                done = L.expire(
                    log, f.item, reason=f"{ACTOR}: crashed lane, work kept for recovery"
                )
                moves.append(
                    _move(
                        "expire",
                        f.item,
                        done,
                        f"recorded {f.item} expired; `ddflow recover` lists it",
                    )
                )
            else:
                moves.append(
                    _move(
                        "expire",
                        f.item,
                        False,
                        f"would record {f.item} expired so it can be recovered",
                    )
                )
    if cycles:
        res = AOS.run(log, st, cfg, [c.as_dict() for c in cycles], mode)
        moves += [
            _move("order", "", ln.startswith("auto-order added"), ln) for ln in AOS.lines(res)
        ]
    return moves


def record_run(log: EventLog, mode: str, findings: int, moves: list[dict[str, Any]]) -> None:
    """The periodic pass ran: `cadence.ran`, which is what `due` reads."""
    done = sum(1 for m in moves if m["applied"])
    note = f"mode {mode}: {findings} finding(s), {done} correction(s) applied"
    log.append("cadence.ran", CADENCE, {"result": "0", "evidence": {"note": note}})


def due(ctx) -> list[dict[str, Any]]:
    """`cadence` evaluator: the watchdog pass is due when lanes exist and the last pass is older
    than `[health].interval_s` (never run counts as due)."""
    h = ctx.cfg.health
    if h.mode == "off" or h.interval_s <= 0:
        return []
    if not any(
        i.lease is not None and not i.terminal and not i.removed for i in ctx.st.items.values()
    ):
        return []
    now = time.time() if ctx.now is None else ctx.now
    last = last_run_epoch(ctx.st, CADENCE)
    if last and now - last < h.interval_s:
        return []
    since: Any = int(now - last) if last else "no pass"
    unit = "s (`ddflow health --run`)" if last else "recorded (`ddflow health --run`)"
    return [{"cadence": CADENCE, "since": since, "every": h.interval_s, "unit": unit}]
