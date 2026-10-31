"""The fleet registry: every agent the log has seen, with its class and last stop reason.

Not a second store: a fold over the lane ledger (`services.lanes`, which already derives a
lane's state from leases, heartbeats and sessions) plus the stop reasons the log already
carries (`lease.expired` reason, `lease.released` note, `session.ended` summary). The log
is the durable part; this is a pure function of (events, ledger, state, config, now).
`resume` is a pure answer too: asking twice gives the same answer, and it names a lane
another agent has already taken over instead of offering a second restart.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ..config import Config
from ..core.clock import epoch
from ..core.events import Event
from ..core.records import State
from . import lanes as LN
from .lanes import states as S

WORKING, STALLED, FAILED, IDLE, DONE = "working", "stalled", "failed", "idle", "done"
#: Urgency order of the classes in a listing: lost work first.
ORDER = (FAILED, STALLED, IDLE, WORKING, DONE)
#: Lane-ledger states that mean "stopped with its item unfinished".
_STOPPED = S.UNFINISHED
_RELEASE_OK = ("completed", "done", "merged")


@dataclass(frozen=True)
class Entry:
    name: str
    cls: str
    harness: str = ""
    model: str = ""
    item: str = ""
    lane_state: str = ""
    heartbeat: float = 0.0
    last_event: float = 0.0
    stop_reason: str = ""
    stop_at: float = 0.0
    worktree: str = ""
    session: str = ""

    def as_dict(self, now: float) -> dict[str, Any]:
        d = asdict(self)
        d["heartbeat_age_s"] = max(0, int(now - self.heartbeat)) if self.heartbeat else None
        return d


def _signals(events: Iterable[Event], cap: int) -> dict[str, tuple[str, str]]:
    """Agent -> (ts, reason): the newest reason the log gives for that agent having stopped."""
    out: dict[str, tuple[str, str]] = {}
    sessions: dict[str, str] = {}  # session id -> agent
    for e in events:
        who, text = "", ""
        if e.kind == "session.started":
            sessions[e.subject] = e.agent
        elif e.kind == "session.ended":
            who, text = sessions.get(e.subject, e.agent), str(e.data.get("summary") or "")
        elif e.kind == "lease.expired":
            who, text = str(e.data.get("holder") or e.agent), str(e.data.get("reason") or "")
        elif e.kind == "lease.released":
            who, text = str(e.data.get("holder") or e.agent), str(e.data.get("note") or "")
            if text.strip().lower() in _RELEASE_OK:
                text = ""
        if who and text.strip() and e.ts >= out.get(who, ("", ""))[0]:
            out[who] = (e.ts, LN.safe(text)[:cap] if cap > 0 else "")
    return out


def _class(lane: LN.Lane, reason: str, patterns: Sequence[re.Pattern[str]]) -> str:
    if lane.state in _STOPPED:
        return FAILED if any(p.search(reason) for p in patterns) else STALLED
    if lane.state == S.STOPPED_DONE:
        return DONE
    if lane.state == S.STALE:
        return STALLED
    return WORKING if lane.state == S.WORKING else IDLE


def _patterns(cfg: Config) -> list[re.Pattern[str]]:
    out = []
    for p in cfg.lanes.fleet_failed_patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue  # a bad pattern is skipped here; the knob check names it
    return out


def ledger(events: Sequence[Event], st: State, cfg: Config, now: float, plan: Any) -> list[LN.Lane]:
    """The lane ledger over ``[lanes] fleet_window_s`` (0: all history) instead of window_s."""
    wide = copy.copy(cfg)
    wide.lanes = copy.copy(cfg.lanes)
    win = cfg.lanes.fleet_window_s
    wide.lanes.window_s = win if win > 0 else max(int(now), 1)
    return LN.build(events, st, wide, now, plan)


def registry(
    events: Sequence[Event], ledger: Sequence[LN.Lane], st: State, cfg: Config, now: float
) -> list[Entry]:
    """Every lane in ``ledger``, classed, with its newest stop reason, most urgent first."""
    sig = _signals(events, cfg.lanes.fleet_reason_chars)
    pats = _patterns(cfg)
    rows = []
    for ln in ledger:
        at, reason = sig.get(ln.name, ("", ""))
        if ln.state not in _STOPPED and ln.state != S.STOPPED_DONE:
            reason, at = "", ""
        rows.append(
            Entry(
                name=ln.name,
                cls=_class(ln, reason, pats),
                harness=ln.harness,
                model=ln.model,
                item=ln.item,
                lane_state=ln.state,
                heartbeat=ln.heartbeat,
                last_event=ln.last_event,
                stop_reason=reason,
                stop_at=epoch(at) if at else 0.0,
                worktree=ln.worktree,
                session=ln.session,
            )
        )
    return sorted(rows, key=lambda e: (ORDER.index(e.cls), e.name))


def resume(entries: Sequence[Entry], name: str, st: State, now: float) -> dict[str, Any]:
    """The one resume for ``name``: eligible only while its item is unfinished and unheld.

    Pure and idempotent: nothing is spawned or recorded, so asking again is the same answer;
    once another agent holds the item the answer turns to "already taken over".
    """
    found = LN.find([_as_lane(e) for e in entries], name)
    base: dict[str, Any] = {"lane": name, "eligible": False, "item": "", "command": ""}
    if found is None:
        return {**base, "reason": f"no agent {LN.safe(name)!r} in the registry"}
    e = next(x for x in entries if x.name == found.name)
    base.update(lane=e.name, item=e.item, worktree=e.worktree, stop_reason=e.stop_reason)
    it = st.items.get(e.item)
    ls = it.lease if it and not it.terminal else None
    if ls and ls.holder != e.name:
        return {**base, "reason": f"already taken over: {e.item} is held by {ls.holder}"}
    if e.lane_state not in _STOPPED:
        return {**base, "reason": f"{e.name} is {e.cls} ({e.lane_state}); nothing to resume"}
    if it is None or it.terminal:
        return {**base, "reason": f"{e.item} is finished; nothing to resume"}
    return {
        **base,
        "eligible": True,
        "reason": f"{e.name} stopped with {e.item} unfinished",
        "command": f"ddflow lanes brief {LN.safe(e.name)}",
    }


def _as_lane(e: Entry) -> LN.Lane:
    return LN.Lane(name=e.name)


def render(entries: Sequence[Entry], now: float) -> str:
    """One line per agent: name, class, harness, model, item, heartbeat age, stop reason."""
    if not entries:
        return "no agents seen"
    head = ("AGENT", "CLASS", "HARNESS", "MODEL", "ITEM", "BEAT", "STOP REASON")
    rows = [head]
    for e in entries:
        age = f"{max(0, int(now - e.heartbeat)) // 60}m" if e.heartbeat else "-"
        rows.append(
            (
                LN.safe(e.name),
                e.cls,
                LN.safe(e.harness) or "-",
                LN.safe(e.model) or "-",
                LN.safe(e.item) or "-",
                age,
                e.stop_reason or "-",
            )
        )
    w = [max(len(r[i]) for r in rows) for i in range(len(head) - 1)]
    return "\n".join(
        "  ".join([c.ljust(n) for c, n in zip(r, w, strict=False)] + [r[-1]]).rstrip() for r in rows
    )
