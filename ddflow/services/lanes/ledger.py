"""Events to lanes: the ledger, a pure function of (events, state, config, now, plan).

Nothing here reads a clock, a process table or a file: `now` and the schedule `plan` are
arguments, so the same log and the same `now` always give the same ledger.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import ddflow.services.lanes.states as S

from ...config import Config
from ...core.clock import epoch
from ...core.events import Event
from ...core.records import Item, State
from ...core.schedule import Plan
from .. import waiting as WG

#: The event kinds that make an agent a lane.
_LANE_KINDS = ("session.", "lease.")
#: Blocked reasons that mean "another lane's lease or globs hold it".
_CONFLICT = ("conflict", "contested")


@dataclass(frozen=True)
class Stall:
    """A ready-but-blocked item: who holds what it needs."""

    item: str
    holder: str  #: the lane holding the blocking lease ("" when none is live)
    held_item: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Lane:
    name: str
    harness: str = ""
    model: str = ""
    item: str = ""  #: the item it holds, else the last one it held
    state: str = S.IDLE
    since: float = 0.0  #: epoch seconds the state began
    heartbeat: float = 0.0  #: epoch seconds of the last lease heartbeat (0: none)
    last_event: float = 0.0
    worktree: str = ""
    session: str = ""
    detail: str = ""
    ready: tuple[str, ...] = ()
    stalls: tuple[Stall, ...] = field(default=())
    #: Its standing `lease.waiting` records: "<item> waits for <held item> (<holder>)".
    waits: tuple[str, ...] = ()

    def as_dict(self, now: float) -> dict[str, Any]:
        d = asdict(self)
        d["stalls"] = [s.as_dict() for s in self.stalls]
        d["ready"] = list(self.ready)
        d["waits"] = list(self.waits)
        d["for_s"] = max(0, int(now - self.since)) if self.since else 0
        d["heartbeat_age_s"] = max(0, int(now - self.heartbeat)) if self.heartbeat else None
        return d


@dataclass
class _Facts:
    """What the events say about one agent, before it is classified."""

    last_ts: str = ""
    lane: bool = False
    session: str = ""
    session_ts: str = ""
    harness: str = ""
    model: str = ""
    item: str = ""
    item_ts: str = ""
    released_ts: str = ""
    beat_ts: str = ""


def _scan(events: Iterable[Event]) -> dict[str, _Facts]:
    facts: dict[str, _Facts] = {}
    for e in events:
        who = e.agent
        f = facts.setdefault(who, _Facts())
        f.last_ts = max(f.last_ts, e.ts)
        if not e.kind.startswith(_LANE_KINDS):
            continue
        f.lane = True
        if e.kind == "session.started" and e.ts >= f.session_ts:
            f.session, f.session_ts = e.subject, e.ts
            f.harness = str(e.data.get("tool", "") or f.harness)
            f.model = str(e.data.get("model", "") or f.model)
        elif e.kind == "lease.acquired":
            holder = str(e.data.get("holder") or who)
            h = facts.setdefault(holder, _Facts())
            h.lane = True
            h.last_ts = max(h.last_ts, e.ts)
            if e.ts >= h.item_ts:
                h.item, h.item_ts = e.subject, e.ts
        elif e.kind == "lease.renewed":
            h = facts.setdefault(str(e.data.get("holder") or who), _Facts())
            h.lane, h.beat_ts = True, max(h.beat_ts, e.ts)
            h.last_ts = max(h.last_ts, e.ts)
        elif e.kind in ("lease.released", "lease.expired"):
            holder = str(e.data.get("holder") or who)
            h = facts.setdefault(holder, _Facts())
            h.lane = True
            h.released_ts = max(h.released_ts, e.ts)
            h.last_ts = max(h.last_ts, e.ts)
    return facts


def _held(st: State, name: str) -> Item | None:
    """The unfinished item ``name`` holds a lease on (the freshest heartbeat wins)."""
    mine = [i for i in st.items.values() if i.lease and i.lease.holder == name and not i.terminal]
    return max(mine, key=lambda i: (i.lease.renewed_at, i.id)) if mine else None  # type: ignore[union-attr]


def _stalls(st: State, plan: Plan) -> tuple[Stall, ...]:
    out = []
    for b in plan.blocked:
        if b.reason not in _CONFLICT:
            continue
        for held in b.waiting_on or [""]:
            it = st.items.get(held)
            holder = it.lease.holder if it and it.lease else ""
            out.append(Stall(b.item, holder, held, b.detail))
    return tuple(out)


def _free(name: str, f: _Facts, st: State, plan: Plan, ended: bool, last: float) -> Lane:
    it = st.items.get(f.item)
    finished = it is None or it.terminal
    since = max(epoch(f.released_ts), last) if f.released_ts else last
    base: dict[str, Any] = {
        "name": name,
        "item": f.item,
        "since": since,
        "last_event": last,
        "heartbeat": epoch(f.beat_ts) if f.beat_ts else 0.0,
    }
    if ended:
        state = S.STOPPED_DONE if finished else S.STOPPED_ENDED
        return Lane(state=state, detail="its last session ended", **base)
    ready = tuple(i.id for i in plan.ready)
    if ready:
        return Lane(state=S.IDLE_READY, ready=ready, **base)
    stalls = _stalls(st, plan)
    if stalls:
        who = ", ".join(sorted({s.holder for s in stalls if s.holder}))
        return Lane(state=S.WAIT_GLOB, stalls=stalls, detail=f"blocked by {who}", **base)
    deps = [b for b in plan.blocked if b.reason == "deps"]
    if deps:
        d = deps[0]
        return Lane(state=S.WAIT_DEP, detail=f"{d.item}: {d.detail}", **base)
    return Lane(state=S.IDLE, **base)


def _lane(name: str, f: _Facts, st: State, cfg: Config, now: float, plan: Plan) -> Lane:
    sess = st.sessions.get(f.session)
    ended = bool(sess and sess.ended_at)
    last = epoch(f.last_ts)
    held = _held(st, name)
    if held is None or held.lease is None:
        lane = _free(name, f, st, plan, ended, last)
    else:
        ls = held.lease
        stale_after = cfg.lanes.stale_after_s or cfg.lease.ttl_s
        # A session that ended BEFORE the last heartbeat did not end this work.
        ended_at = epoch(sess.ended_at) if ended and sess else 0.0
        state = S.held_state(
            age_s=now - ls.renewed_at,
            stale_after_s=stale_after,
            lapsed=not ls.live(now, cfg.lease.grace_s),
            session_ended=ended and ended_at >= ls.renewed_at,
        )
        began = {
            S.WORKING: ls.acquired_at,
            S.STALE: ls.renewed_at + stale_after,
            S.STOPPED_ENDED: ended_at,
        }
        lane = Lane(
            name=name,
            item=held.id,
            state=state,
            since=began.get(state, ls.renewed_at + ls.ttl_s),
            heartbeat=ls.renewed_at,
            last_event=last,
            worktree=ls.worktree or held.worktree,
            detail=ls.note if state == S.WORKING else "",
        )
    return _with_session(lane, f, st)


def _with_session(lane: Lane, f: _Facts, st: State) -> Lane:
    worktree = lane.worktree or (st.items[f.item].worktree if f.item in st.items else "")
    return Lane(
        **{
            **lane.__dict__,
            "harness": f.harness,
            "model": f.model,
            "session": f.session,
            "worktree": worktree,
        }
    )


def _with_waits(lane: Lane, st: State, cfg: Config, now: float) -> Lane:
    """``lane`` with the files it waits for (`services.waiting`), if it does."""
    mine = tuple(
        f"{w['item']} waits for "
        + ", ".join(f"{o['item']} ({o['holder']})" for o in w["waiting_on"])
        for w in WG.rows(st, cfg, now)
        if w["agent"] == lane.name
    )
    return replace(lane, waits=mine) if mine else lane


def build(events: Sequence[Event], st: State, cfg: Config, now: float, plan: Plan) -> list[Lane]:
    """Every lane active inside ``cfg.lanes.window_s`` (or holding a live lease), by name."""
    horizon = now - cfg.lanes.window_s
    lanes = []
    for name, f in _scan(events).items():
        if not f.lane:
            continue
        held = _held(st, name)
        live = bool(held and held.lease and held.lease.live(now, cfg.lease.grace_s))
        if live or epoch(f.last_ts) >= horizon:
            lanes.append(_with_waits(_lane(name, f, st, cfg, now, plan), st, cfg, now))
    return sorted(lanes, key=lambda ln: ln.name)


def find(lanes: Sequence[Lane], name: str) -> Lane | None:
    """The lane called ``name``: exactly, else the one lane whose name contains it."""
    exact = [ln for ln in lanes if ln.name == name]
    if exact:
        return exact[0]
    near = [ln for ln in lanes if name and name in ln.name]
    return near[0] if len(near) == 1 else None
