"""How a session ended: the in-flight note, the interrupted close and the hand-over.

The Stop and SessionEnd hooks write what the session was doing as a note; a session that
died without an end (its lease lapsed and it wrote nothing after) is closed `interrupted`
by the next SessionStart; and the next session is shown the previous one's last prompt
and note. Every decision here is a pure function of the events and the folded state, so a
test seeds a log with explicit times and the hooks only add the writes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core import clock
from ...core import provenance as PV
from ...core.model import fold
from ...infra.log import EventLog
from ..gates.defs import pipeline_for
from . import _record as R

#: The `source` of the note the Stop and SessionEnd hooks write, so the next one can tell
#: it from the operator's and the agent's own notes and from a compaction's.
INFLIGHT_SOURCE = "session-lifecycle:inflight"
#: The reasons Claude Code names for a SessionEnd; anything else is `other`.
END_REASONS = ("clear", "resume", "logout", "prompt_input_exit", "other")
#: `session.ended`'s `how`: the hook saw it end, or a later session found it dead.
HOW_ENDED = "ended"
HOW_INTERRUPTED = "interrupted"


def _gates_phrase(item: Any, cfg: Config) -> str:
    """`last gate critic passed, next gate ci` for one held item, or "" when no gate ran."""
    done = [g for g in item.gates.values() if g.outcome]
    if not done:
        return ""
    last = max(done, key=lambda g: g.at or "")
    try:
        pending = [g for g in pipeline_for(item, cfg) if g not in item.gates]
    except Exception:  # a pipeline that cannot be read must not stop the note
        pending = []
    text = f"last gate {last.gate} {last.outcome}"
    return f"{text}, next gate {pending[0]}" if pending else text


def held_labels(st: Any, agent: str, cfg: Config) -> list[str]:
    """One label per item `agent` holds: `B-x (last gate critic passed, next gate ci)`."""
    held = sorted(
        (i for i in st.items.values() if i.lease is not None and i.lease.holder == agent),
        key=lambda i: i.id,
    )
    return [f"{i.id} ({e})" if (e := _gates_phrase(i, cfg)) else i.id for i in held]


def inflight_text(st: Any, agent: str, cfg: Config) -> str:
    """`holding B-x (last gate critic passed, next gate ci); holding ...`, or `holding
    nothing` when the agent holds no item."""
    held = held_labels(st, agent, cfg)
    return "holding " + "; ".join(held) if held else "holding nothing"


def _last_inflight(events: list, sid: str) -> str:
    for ev in reversed(events):
        if (
            ev.kind == "session.note"
            and ev.subject == sid
            and ev.data.get("source") == INFLIGHT_SOURCE
        ):
            return str(ev.data.get("text", ""))
    return ""


def record_inflight(log: EventLog, cfg: Config, sid: str, events: list, st: Any, agent: str) -> str:
    """Write the in-flight note for `sid` unless it says what the session's last one said.

    `holding nothing` is written only when an earlier note said otherwise, so a session
    that never held anything leaves no note. Returns what happened."""
    text = inflight_text(st, agent, cfg)
    clean, _n = R.redact(text, cfg)
    before = _last_inflight(events, sid)  # stored redacted, so compared redacted
    if clean == before or (text == "holding nothing" and not before):
        return "unchanged"
    log.append("session.note", sid, {"text": clean, "item": "", "source": INFLIGHT_SOURCE})
    return "recorded"


def end_reason(raw: Any) -> str:
    """A harness's end reason as one of the fixed words: it is free text from outside."""
    word = str(raw or "").strip().lower()
    return word if word in END_REASONS else "other"


is_ended = R._is_ended  # a `resumed` start reopens an ended session


def close_ended(log: EventLog, cfg: Config, sid: str, events: list, st: Any, reason: str) -> str:
    """SessionEnd: the in-flight note, then the end. The reason is the summary only when
    the session has none of its own, so an explicit `session end --summary` is not buried."""
    if is_ended(events, sid):
        return "already ended"
    sess = st.sessions.get(sid)
    if sess is not None and cfg.session.inflight_note:
        record_inflight(log, cfg, sid, events, st, sess.agent or log.agent_id)
    summary = "" if sess is not None and sess.summary else f"session ended ({end_reason(reason)})"
    clean, _n = R.redact(summary, cfg)
    log.append("session.ended", sid, {"summary": clean, "how": HOW_ENDED})
    return "ended"


@dataclass(frozen=True)
class Dead:
    """A session found dead: where it stopped and the lapse that proves it."""

    session: str
    agent: str
    last_active: str
    lapse_at: str


def _epoch(ts: str) -> float:
    return clock.epoch(ts)


def _lapses(events: list, st: Any, agent: str, now: float, grace_s: int) -> list[float]:
    """When `agent`'s leases lapsed: from `lease.expired` events and from the state's lapsed
    leases (a reclaim replaces the lease in state, the event stays in the log).

    A `lease.expired` is often written by a reaper long after the lease ran out, so its own
    time is only the latest the lapse can be: the lapse is the TTL end of the holder's last
    acquisition or renewal of that item before the event, when the log has one."""
    last_ttl_end: dict[str, float] = {}  # item -> end of the holder's latest known TTL
    out: list[float] = []
    for e in events:
        d = e.data
        if e.kind in ("lease.acquired", "lease.renewed") and d.get("holder", e.agent) == agent:
            at = d.get("at")
            if isinstance(at, (int, float)):
                prior = last_ttl_end.get(e.subject, 0.0)
                ttl = d.get("ttl_s")
                end = at + (ttl if isinstance(ttl, (int, float)) else 0)
                last_ttl_end[e.subject] = max(prior, end) if e.kind == "lease.renewed" else end
        elif e.kind == "lease.expired" and d.get("holder") == agent:
            wrote = _epoch(e.ts)
            out.append(min(wrote, last_ttl_end[e.subject]) if e.subject in last_ttl_end else wrote)
    for it in st.items.values():
        lease = it.lease
        if lease is not None and lease.holder == agent and not lease.live(now, grace_s):
            out.append(lease.renewed_at + lease.ttl_s)
    return out


def _activity(events: list) -> tuple[dict[str, str], dict[str, bool]]:
    """Each session's last write, and whether it is ended NOW (a `resumed` start reopens)."""
    last: dict[str, str] = {}
    ended: dict[str, bool] = {}
    for ev in events:
        if ev.kind.startswith("session.") and ev.subject:
            last[ev.subject] = max(last.get(ev.subject, ""), ev.ts)
            if ev.kind == "session.ended":
                ended[ev.subject] = True
            elif ev.kind == "session.started" and ev.data.get("resumed"):
                ended[ev.subject] = False
    return last, ended


def find_dead(
    events: list, st: Any, cfg: Config, *, current: str = "", now: float | None = None
) -> list[Dead]:
    """Open sessions that are dead: not `current`, their agent holds no live lease, a lease
    of the agent lapsed at or after the session began, and the session wrote nothing since."""
    now = time.time() if now is None else now
    grace = cfg.lease.grace_s
    last, ended = _activity(events)
    live_agents = {
        it.lease.holder
        for it in st.items.values()
        if it.lease is not None and it.lease.live(now, grace)
    }
    lapses: dict[str, list[float]] = {}
    out: list[Dead] = []
    for sid, sess in sorted(st.sessions.items(), key=lambda kv: kv[1].started_at or ""):
        if sid == current or ended.get(sid) or not sess.agent or sess.agent in live_agents:
            continue
        if sess.agent not in lapses:
            lapses[sess.agent] = _lapses(events, st, sess.agent, now, grace)
        began = _epoch(sess.started_at) if sess.started_at else 0.0
        hits = [t for t in lapses[sess.agent] if t >= began]
        if hits and _epoch(last.get(sid, "")) <= max(hits):  # nothing written after the lapse
            seen = last.get(sid, sess.started_at or "")
            out.append(Dead(sid, sess.agent, seen, clock.iso_at(max(hits))))
    return out


def close_interrupted(
    log: EventLog,
    cfg: Config,
    *,
    current: str = "",
    dry_run: bool = False,
    now: float | None = None,
) -> list[Dead]:
    """Close the dead sessions `interrupted`, at most `[session].interrupted_close_max` per
    pass, oldest first; `dry_run` names them and writes nothing. The events are those of
    the log as it stands, so adopt orphans first if the nearest session should count."""
    cap = cfg.session.interrupted_close_max
    if cap <= 0:
        return []
    events = log.read_all()
    st = fold(events, strict=False)
    dead = find_dead(events, st, cfg, current=current, now=now)[:cap]
    if not dry_run:
        for d in dead:
            log.append(
                "session.ended",
                d.session,
                {
                    "summary": "",
                    "how": HOW_INTERRUPTED,
                    "last_active": d.last_active,
                    "lapse_at": d.lapse_at,
                },
            )
    return dead


def start_housekeeping(
    log: EventLog, cfg: Config, *, current: str = "", dry_run: bool = False
) -> dict[str, int]:
    """What SessionStart does before it shows anything: adopt the orphans, then close the
    dead sessions. Nothing is written on a dry run."""
    adopted = 0 if dry_run else R.adopt_orphans(log)
    closed = close_interrupted(log, cfg, current=current, dry_run=dry_run)
    return {"adopted": adopted, "interrupted": len(closed)}


def previous_lines(events: list, st: Any, agent: str, current: str, cfg: Config) -> list[str]:
    """The previous session's last prompt and note as fenced data, for the next session's
    start; [] when the knob is off or this agent has no earlier closed session."""
    chars = cfg.session.previous_session_chars
    if chars <= 0:
        return []
    closed = [
        s
        for s in st.sessions.values()
        if s.agent == agent
        and s.ended_at
        and s.id != current
        and (s.prompts or s.notes or s.summary)
    ]
    if not closed:
        return []
    prev = max(closed, key=lambda s: s.ended_at)
    origin = PV.Origin(PV.AGENT, by=prev.agent)
    lines = [f"## Previous session ({prev.id}, ended {prev.ended_at})", ""]
    if prev.summary:
        lines.append(PV.fence("session-summary", prev.id, prev.summary[:chars], origin))
    if prev.prompts:
        text = str(prev.prompts[-1].get("text", ""))
        lines.append(PV.fence("session-prompt", prev.id, text[:chars], origin))
    if prev.notes:
        text = str(prev.notes[-1].get("text", ""))
        lines.append(PV.fence("session-note", prev.id, text[:chars], origin))
    return [*lines, ""]


def harness_sid(raw: str) -> str:
    """The ddflow session id a harness's own session id maps to ("" when it gave none)."""
    return R.harness_session_id(raw) if raw else ""
