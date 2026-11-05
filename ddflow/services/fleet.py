"""The fleet registry: every agent the log has seen, with its class and last stop reason.

Not a second store: a fold over the lane ledger (`services.lanes`, which already derives a
lane's state from leases, heartbeats and sessions) plus the stop reasons the log already
carries (`lease.expired` reason, `lease.released` note, `session.ended` summary). The log
is the durable part; this is a pure function of (events, ledger, state, config, now).
`resume` is a pure answer too: asking twice gives the same answer, and it names a lane
another agent has already taken over instead of offering a second restart. It starts nothing:
restarting is `ddflow resume <item>` (DX-resume-command) and a lane launch.
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
#: Urgency order of the classes in a listing: lost work, then live work, so a capped listing
#: keeps both ahead of the long tail of agents that went quiet with nothing unfinished.
ORDER = (FAILED, STALLED, WORKING, IDLE, DONE)
#: Lane-ledger states that mean "stopped with its item unfinished".
_STOPPED = S.UNFINISHED
#: Characters of a reason that failure patterns are matched against (display is cut separately).
RAW_CAP = 2000
#: Release notes that say the work ended normally. No note says nothing either way.
_RELEASE_OK = ("completed", "done", "merged")


@dataclass(frozen=True)
class Entry:
    name: str
    cls: str
    role: str = ""  #: from `session.started`; empty while a session records none
    harness: str = ""
    model: str = ""
    item: str = ""
    items: tuple[str, ...] = ()  #: items it leased, newest first, cut to `fleet_items`
    items_total: int = 0
    lease_s: int = 0  #: seconds left on its live lease of ``item``; 0 when it holds none
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


def _holder(e: Event) -> str:
    return str(e.data.get("holder") or e.agent)


def _word(e: Event, sessions: dict[str, str]) -> tuple[str, str, bool]:
    """(agent, stop reason, whether it clears an older one) that ``e`` says, if anything."""
    if e.kind == "session.started":
        return e.agent, "", True
    if e.kind == "session.ended":
        return sessions.get(e.subject, e.agent), str(e.data.get("summary") or ""), False
    if e.kind == "lease.acquired":
        return _holder(e), "", True
    if e.kind == "lease.expired":
        return _holder(e), str(e.data.get("reason") or ""), False
    if e.kind == "lease.released":
        note = str(e.data.get("note") or "")
        if note.strip().lower() not in _RELEASE_OK:
            return _holder(e), note, False
        # Only the holder's own release shows it carried on; a sweep says nothing about it.
        return _holder(e), "", str(e.data.get("by") or e.agent) == _holder(e)
    return "", "", False


def _signals(events: Iterable[Event]) -> dict[str, tuple[str, str]]:
    """Agent -> (ts, reason): why it stopped, as far as the log says.

    The newest word wins, and a sign the agent carried on after a stop (a normal release, a
    new lease, a new session) is a word too: it clears the older reason, which no longer
    explains anything.
    """
    out: dict[str, tuple[str, str]] = {}
    sessions: dict[str, str] = {}  # session id -> agent
    for e in events:
        if e.kind == "session.started":
            sessions[e.subject] = e.agent
        who, text, clears = _word(e, sessions)
        if who and (clears or text.strip()) and e.ts >= out.get(who, ("", ""))[0]:
            out[who] = (e.ts, text[:RAW_CAP])
    return {who: sig for who, sig in out.items() if sig[1]}


def _history(events: Iterable[Event]) -> dict[str, tuple[str, list[str]]]:
    """Agent -> (role, items it leased, newest first). The roles the sessions recorded."""
    roles: dict[str, str] = {}
    items: dict[str, dict[str, str]] = {}  # agent -> item -> newest lease ts
    for e in events:
        if e.kind == "session.started" and e.data.get("role"):
            roles[e.agent] = str(e.data["role"])
        elif e.kind == "lease.acquired":
            held = items.setdefault(_holder(e), {})
            held[e.subject] = max(held.get(e.subject, ""), e.ts)
    return {
        who: (
            roles.get(who, ""),
            sorted(items.get(who, {}), key=items.get(who, {}).get, reverse=True),
        )
        for who in roles.keys() | items.keys()
    }


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
            continue  # as the knob's doc says, a pattern that does not compile is skipped
    return out


def ledger(events: Sequence[Event], st: State, cfg: Config, now: float, plan: Any) -> list[LN.Lane]:
    """The lane ledger over ``[lanes] fleet_window_s`` (0: all history) instead of window_s."""
    wide = copy.copy(cfg)
    wide.lanes = copy.copy(cfg.lanes)
    win = cfg.lanes.fleet_window_s
    wide.lanes.window_s = win if win > 0 else max(int(now), 1)
    # The registry shows no waits, and over all history they cost agents x waits x leases.
    quiet = copy.copy(st)
    quiet.waiting = {}
    return LN.build(events, quiet, wide, now, plan)


def registry(
    events: Sequence[Event], ledger: Sequence[LN.Lane], st: State, cfg: Config, now: float
) -> list[Entry]:
    """Every lane in ``ledger``, classed, with its newest stop reason: most urgent class first,
    the most recently heard from first inside a class."""
    sig = _signals(events)
    history = _history(events)
    pats = _patterns(cfg)
    keep = max(0, cfg.lanes.fleet_items)
    rows = []
    for ln in ledger:
        at, reason = sig.get(ln.name, ("", ""))
        if ln.state not in _STOPPED:
            reason, at = "", ""
        role, items = history.get(ln.name, ("", []))
        rows.append(
            Entry(
                name=ln.name,
                cls=_class(ln, reason, pats),
                role=role,
                harness=ln.harness,
                model=ln.model,
                item=ln.item,
                items=tuple(items[:keep]),
                items_total=len(items),
                lease_s=_lease_left(ln, st, now, cfg.lease.grace_s),
                lane_state=ln.state,
                heartbeat=ln.heartbeat,
                last_event=ln.last_event,
                stop_reason=LN.safe(reason)[: max(0, cfg.lanes.fleet_reason_chars)],
                stop_at=epoch(at) if at else 0.0,
                worktree=ln.worktree,
                session=ln.session,
            )
        )
    return sorted(rows, key=lambda e: (ORDER.index(e.cls), -e.last_event, e.name))


def _lease_left(ln: LN.Lane, st: State, now: float, grace_s: float) -> int:
    it = st.items.get(ln.item)
    ls = it.lease if it and not it.terminal else None
    if ls is None or ls.holder != ln.name or not ls.live(now, grace_s):
        return 0
    return max(1, int(ls.remaining_s(now)))  # past the ttl but in the grace it is still held


def resume(
    entries: Sequence[Entry], name: str, st: State, now: float, grace_s: float = 0.0
) -> dict[str, Any]:
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
    who, what = LN.safe(e.name), LN.safe(e.item)  # the log's words: never printed raw
    it = st.items.get(e.item)
    ls = it.lease if it and not it.terminal else None
    if ls and ls.holder == e.name and ls.live(now, grace_s):
        return {**base, "reason": f"{who} still holds {what} on a live lease"}
    if ls and ls.holder and ls.holder != e.name and ls.live(now, grace_s):
        return {**base, "reason": f"already taken over: {what} is held by {LN.safe(ls.holder)}"}
    if e.lane_state not in _STOPPED:
        return {**base, "reason": f"{who} is {e.cls} ({e.lane_state}); nothing to resume"}
    if it is None or it.terminal:
        return {**base, "reason": f"{what} is finished; nothing to resume"}
    return {
        **base,
        "eligible": True,
        "reason": f"{who} stopped with {what} unfinished; start a session with its brief "
        "(this view starts nothing: a lane launches only from an approved template)",
        "command": f"ddflow lanes brief {LN.safe(e.name)}",
    }


def _as_lane(e: Entry) -> LN.Lane:
    return LN.Lane(name=e.name)


def render(entries: Sequence[Entry], now: float, total: int = 0) -> str:
    """One line per agent: name, class, harness, model, item, heartbeat age, stop reason.

    ``total`` is the size of the registry when ``entries`` is a cut of it: the cut says so.
    """
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
    lines = [
        "  ".join([c.ljust(n) for c, n in zip(r, w, strict=False)] + [r[-1]]).rstrip() for r in rows
    ]
    if total > len(entries):
        lines.append(f"... and {total - len(entries)} more (lanes.fleet_max_rows; 0 lists all)")
    return "\n".join(lines)
