"""The watchdog's facts and its corrections (DX-health-watch).

`gather` reads the folded state, the log and the worktrees into the plain values
`core.health.inspect` judges. `correct` turns findings into the moves the per-kind modes
(`[health].mode`, overridden by `mode_<kind>`) allow:

* ``off``      nothing;
* ``propose``  one line per move it WOULD make, nothing written;
* ``apply``    only moves that lose nothing, each an event that already exists, as a ladder:
  - IDLE        a nudge is recorded (once per lease);
  - STALLED     a lane another item waits for is asked to STOP (recorded, shown to the holder
                at its next heartbeat);
  - UNRESPONSIVE the stop request went unanswered for `[health].reply_s`: the holder is PARKED
                (a park record: worktree, branch, what it waits for) and only then its lease is
                broken exactly as `claim --break` breaks it (`lease.expired` naming who and why;
                branch, worktree and gates kept);
  - a deadlock gets its `needs` edges from `services.autoorder` (`task.updated`, auto_order);
  - CRASHED     a lane with work has its lease recorded expired (`lease.expired`), the state
                `ddflow recover` and any claim pick up.
  A RUNAWAY job is only reported. Nothing here signals a process, removes a worktree, a branch
  or a commit, or releases a lane that is making progress.

Nudge, stop and park records are `cadence.ran` events under ``health_<step>:<item>`` (the
evidence names the lease they belong to), until DX-park-and-resume gives parking its own record.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core import clock
from ..core import health as H
from ..core import leasewait as LW
from ..infra import harness_identity as HI
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
STEPS = ("nudge", "stop", "park")
#: `(agent) -> (alive? or None when unknown, pids)`: is the harness that declared it running
Alive = Callable[[str], "tuple[bool | None, list[int]]"]


def limits(cfg) -> H.Limits:
    h = cfg.health
    return H.Limits(h.stall_s, h.runaway_s, h.idle_s, h.reply_s, h.crash_s)


def modes(cfg, override: str = "") -> dict[str, str]:
    """The mode of every finding kind: ``override`` for all, else `mode_<kind>` or `mode`."""
    h = cfg.health
    own = {
        H.IDLE: h.mode_idle,
        H.STALLED: h.mode_stalled,
        H.UNRESPONSIVE: h.mode_unresponsive,
        H.LOCKED: h.mode_locked,
        H.CRASHED: h.mode_crashed,
        H.RUNAWAY: h.mode_runaway,
    }
    out = {k: override or (h.mode if own[k] == "inherit" else own[k]) for k in H.KINDS}
    return out


def enabled(by_kind: dict[str, str]) -> bool:
    """Is any finding kind's mode other than off: the one answer to "is the watchdog on"."""
    return any(m != "off" for m in by_kind.values())


def steps_of(st, item: str, lease) -> dict[str, float]:
    """Step -> time of the newest nudge, stop request or park record made for THIS lease."""
    out: dict[str, float] = {}
    for step in STEPS:
        for r in st.cadences.get(f"health_{step}:{item}", []):
            ev = r.get("evidence") or {}
            if ev.get("holder") == lease.holder and ev.get("lease") == lease.event:
                out[step] = max(out.get(step, 0.0), clock.epoch(r["at"], naive="local"))
    return out


def stop_notice(st, item: str) -> str:
    """The line the holder reads at its next heartbeat once the watchdog asked it to stop."""
    lease = st.items[item].lease if item in st.items else None
    asked = steps_of(st, item, lease).get("stop") if lease else None
    if not asked:
        return ""
    return (
        f"the watchdog asked {lease.holder} to stop {clock.fmt_age(time.time() - asked)} ago: "
        f"commit what you have and `ddflow release {item}`, or show progress"
    )


def _record(log: EventLog, step: str, item: str, lease, **more: Any) -> None:
    ev = {"step": step, "holder": lease.holder, "lease": lease.event, **more}
    log.append("cadence.ran", f"health_{step}:{item}", {"result": "0", "evidence": ev})


def _newest_events(events, items: set[str]) -> dict[str, float]:
    """Item -> time of its newest event that is not lease bookkeeping (one pass)."""
    out: dict[str, float] = {}
    for e in events:
        if e.subject in items and e.kind not in LB.BOOKKEEPING:
            t = clock.epoch(e.ts)
            if t > out.get(e.subject, 0.0):
                out[e.subject] = t
    return out


def _work(it, lz, repo: Path, cfg) -> bool | None:
    """Does the holder's tree hold work (True), nothing (False) or could not be told (None)."""
    rec = L.Recovery(
        it.id, lz.holder, "expired_lease", lz.worktree or it.worktree, adopted=it.adopted
    )
    rec.branch = lz.branch or it.branch
    L._measure(rec, repo, cfg)
    return rec.salvageable


def _lane(it, st, cfg, repo, now, hint, rec, act, alive, clone) -> H.Lane:
    """One lease as the plain facts `core.health` judges."""
    i, lz = it.id, it.lease
    recorded = not lz.live(lz.renewed_at)  # an expiry is recorded, whatever the clock says
    if not lz.live(now, cfg.lease.grace_s):
        work = None if rec is None else rec.salvageable
        return H.Lane(
            i, lz.holder, False, lz.renewed_at, work, recorded, lz.worktree, lz.renewed_at
        )
    # a job in flight is progress (until it outlives its budget: that is a RUNAWAY)
    at = hint if hint >= now else max(lz.acquired_at, act(lz), hint)
    steps = steps_of(st, i, lz)
    asked = max(steps.get("nudge", 0.0), steps.get("stop", 0.0))
    ok, found = alive(lz.holder) if not L.foreign(lz, clone) else (None, [])
    gone, work = ok is False, False
    if gone:
        work = _work(it, lz, repo, cfg)
    pids = tuple(found) if gone else ()
    return H.Lane(
        i, lz.holder, True, at, work, recorded, lz.worktree, lz.renewed_at, gone, pids, asked
    )


def gather(
    log: EventLog,
    st,
    cfg,
    repo: Path,
    now: float,
    events=None,
    activity: LB.Activity | None = None,
    alive: Alive | None = None,
) -> tuple[list[H.Lane], list[H.Proc], list[H.Wait]]:
    """The lanes (one per lease of an unfinished item), unended jobs and standing waits."""
    events = log.read_all() if events is None else events
    act = activity or LB.tree_activity(repo)
    alive = alive or (lambda who: HI.liveness(repo, who))
    held = {
        i: it
        for i, it in st.items.items()
        if it.lease is not None and not it.removed and not it.terminal
    }
    newest = _newest_events(events, set(held))
    recs = {
        r.item: r for r in L.scan(log, cfg, repo, now=now, state=st) if r.kind == "expired_lease"
    }
    procs = [
        H.Proc(j.id, j.item, clock.epoch(j.started_at), JOBS.status(j).state == "running")
        for j in st.jobs.values()
        if not j.ended_at
    ]
    budget = cfg.health.runaway_s
    busy = {p.item for p in procs if p.running and (budget <= 0 or now - p.started_at < budget)}
    clone = log.clone_id(create=False)
    lanes = [
        _lane(
            it,
            st,
            cfg,
            repo,
            now,
            now if i in busy else newest.get(i, 0.0),
            recs.get(i),
            act,
            alive,
            clone,
        )
        for i, it in held.items()
    ]
    waits = [
        H.Wait(w.item, w.holder, tuple((o["item"], o["holder"]) for o in w.on))
        for w in WT.waiters(st, cfg, now)
    ]
    return lanes, procs, waits


def inspect(
    log, st, cfg, repo, now, cycles: list[LW.Cycle], events=None, activity=None, alive=None
):
    """The findings now, deduplicated by (kind, item, holder)."""
    lanes, procs, waits = gather(log, st, cfg, repo, now, events, activity, alive)
    found = H.inspect(lanes, procs, waits, [c.as_dict() for c in cycles], now, limits(cfg))
    return list({f.key: f for f in found}.values())


def _move(action: str, item: str, applied: bool, line: str) -> dict[str, Any]:
    return {"action": action, "item": item, "applied": applied, "line": line}


def _park_break(log, st, cfg, item: str, events, now, activity, mode) -> dict[str, Any] | None:
    """Park the holder, then break its lease for the first agent that waits (apply); say so (propose)."""
    lease = st.items[item].lease
    asks = WT.waiting_for(st, cfg, item, now)
    if not asks:
        return None
    claimant = asks[0]["item"]
    v = LB.judge(cfg, events, item, lease, [a["item"] for a in asks], now, activity)
    if v is None or not v.breakable:
        return _move(
            "break",
            item,
            False,
            f"{item} is stalled but not yet breakable ([lease].hold_max_s {cfg.lease.hold_max_s}s)",
        )
    text = f"park {lease.holder} and break {item} for {claimant}: {v.line()}"
    if mode != "apply":
        return _move("break", item, False, f"would {text}")
    pair = asks[0]["globs"] or list(lease.globs)
    where = {"worktree": lease.worktree, "branch": lease.branch, "restore_after": claimant}
    if not steps_of(st, item, lease).get("park"):
        _record(log, "park", item, lease, **where)
    log.append("lease.expired", item, LB.breaking_record(lease, claimant, ACTOR, pair, v))
    L._remote_drop(log, item, lease.holder)
    return _move(
        "break",
        item,
        True,
        f"parked {lease.holder} and broke {item} for {claimant}: "
        f"work kept in {lease.worktree or 'its branch'}",
    )


def _idle_move(log, st, f: H.Finding, mode: str) -> dict[str, Any] | None:
    lease = st.items[f.item].lease
    if steps_of(st, f.item, lease):
        return None  # already asked for this lease
    if mode != "apply":
        return _move("nudge", f.item, False, f"would nudge {lease.holder} on {f.item}")
    _record(log, "nudge", f.item, lease, reason=f.detail)
    return _move("nudge", f.item, True, f"nudged {lease.holder} on {f.item}")


def _stalled_move(log, st, cfg, f: H.Finding, events, now, act, mode) -> dict[str, Any] | None:
    if not f.evidence.get("waiters"):
        return None
    lease = st.items[f.item].lease
    asked = steps_of(st, f.item, lease).get("stop")
    waiters = ", ".join(f.evidence["waiters"])
    if asked and cfg.health.reply_s > 0:
        if now - asked >= cfg.health.reply_s:
            return None  # unanswered: the UNRESPONSIVE finding takes it from here
        return _move(
            "stop",
            f.item,
            False,
            f"{f.item}: stop requested {clock.fmt_age(now - asked)} ago; "
            f"waiting up to {cfg.health.reply_s}s ([health].reply_s) for {lease.holder}",
        )
    if not asked and mode != "apply" and cfg.health.reply_s == 0:
        return _park_break(log, st, cfg, f.item, events, now, act, mode)
    if not asked and mode != "apply":
        return _move(
            "stop", f.item, False, f"would ask {lease.holder} to stop {f.item} ({waiters} waits)"
        )
    if not asked:
        _record(log, "stop", f.item, lease, waiters=f.evidence["waiters"], reason=f.detail)
        if cfg.health.reply_s > 0:
            return _move("stop", f.item, True, f"asked {lease.holder} to stop {f.item}")
    return _park_break(log, st, cfg, f.item, events, now, act, mode)


def _crash_move(log, f: H.Finding, mode: str) -> dict[str, Any] | None:
    if f.evidence.get("work") is not True or f.evidence["recorded"]:
        return None
    if mode != "apply":
        return _move(
            "expire", f.item, False, f"would record {f.item} expired so it can be recovered"
        )
    reason = f"{ACTOR}: crashed lane, work kept for recovery"
    done = L.expire(log, f.item, reason=reason)
    return _move("expire", f.item, done, f"recorded {f.item} expired; `ddflow recover` lists it")


def correct(
    log: EventLog,
    st,
    cfg,
    findings: list[H.Finding],
    cycles: list[LW.Cycle],
    mode: str | dict[str, str],
    now: float,
    events=None,
    activity: LB.Activity | None = None,
) -> list[dict[str, Any]]:
    """The moves the per-kind modes allow for ``findings`` (``mode`` is one mode for every kind
    or a kind -> mode map); in ``apply`` they are written to the log."""
    by_kind = dict.fromkeys(H.KINDS, mode) if isinstance(mode, str) else mode
    if not any(m in MODES[1:] for m in by_kind.values()):
        return []
    events = log.read_all() if events is None else events
    act = activity or LB.tree_activity(log.root)
    moves: list[dict[str, Any]] = []
    for f in findings:
        m = by_kind.get(f.kind, "off")
        if m not in MODES[1:]:
            continue
        move = None
        if f.kind == H.IDLE:
            move = _idle_move(log, st, f, m)
        elif f.kind == H.STALLED:
            move = _stalled_move(log, st, cfg, f, events, now, act, m)
        elif f.kind == H.UNRESPONSIVE and f.evidence.get("waiters"):
            move = _park_break(log, st, cfg, f.item, events, now, act, m)
        elif f.kind == H.CRASHED:
            move = _crash_move(log, f, m)
        if move:
            moves.append(move)
    if cycles and by_kind.get(H.LOCKED) in MODES[1:]:
        res = AOS.run(log, st, cfg, [c.as_dict() for c in cycles], by_kind[H.LOCKED])
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
    if not enabled(modes(ctx.cfg)) or h.interval_s <= 0:
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
