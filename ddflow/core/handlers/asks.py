"""The fold of the ask events: what the approvals-and-questions engine asked and was answered.

`ask.requested` opens an ask (a question or an approval of ``digest``), `ask.answered` closes
it with an outcome a PERSON gave, `ask.withdrawn` is the asker taking it back and `ask.consumed` is a spent use of an answered approval (a gated command an agent then ran), and `ask.expired`
is the audit mark a sweep writes for an ask whose time ran out. The fold is pure: whether a
pending ask has EXPIRED is a question of ``expires_at`` against a clock, so it is answered by
`services.ask.engine.status`, never by the fold -- replaying a log at any time yields the same
rows. The engine in `services/ask` writes these events; nothing else does.
"""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State

PENDING = "pending"
ANSWERED = "answered"
WITHDRAWN = "withdrawn"
EXPIRED = "expired"


def asks_of(st: State) -> dict[str, dict[str, Any]]:
    """The folded asks, ask id -> row (created on first use). Held beside `State`'s declared
    fields until `core/records.py` is free to take an `asks` field; every reader goes through
    here, so that move changes this one line."""
    return st.__dict__.setdefault("asks", {})


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _id(ev: Event) -> str:
    return str(ev.data.get("id") or ev.subject)


def h_requested(st: State, ev: Event) -> None:
    d = ev.data
    ask_id = _id(ev)
    if not ask_id or ask_id in asks_of(st):
        return  # the first request owns the id; a replayed duplicate changes nothing
    asks_of(st)[ask_id] = {
        "id": ask_id,
        "type": str(d.get("type", "approval")),
        "subject": str(d.get("subject", "")),
        "text": str(d.get("text", "")),
        "digest": str(d.get("digest", "")),
        "options": list(d.get("options") or []),
        "expires_at": str(d.get("expires_at", "")),
        "code_hash": str(d.get("code_hash", "")),
        "verb": str(d.get("verb", "")),
        "tier": str(d.get("tier", "")),
        "min_level": _int(d.get("min_level")),
        "uses": _int(d.get("uses")) or (1 if d.get("verb") else 0),
        "use_ttl_s": _int(d.get("use_ttl_s")),
        "canon": str(d.get("canon", "")),
        "used": 0,
        "consumed": [],
        "bot_answerable": d.get("bot_answerable") is True,
        "requested_by": ev.agent,
        "requested_at": ev.ts,
        "state": PENDING,
        "outcome": "",
        "answer": "",
        "answered_by": "",
        "answered_digest": "",
        "coded": False,
        "source": "",
        "level": 0,
        "code_presented": "",
        "role": "",
        "via": "",
        "human": False,
        "answered_at": "",
    }


def h_answered(st: State, ev: Event) -> None:
    row = asks_of(st).get(_id(ev))
    if row is None or row["state"] != PENDING:
        return  # an answer to nothing, or a second answer: the first one stands
    d = ev.data
    row.update(
        state=ANSWERED,
        outcome=str(d.get("outcome", "")),
        answer=str(d.get("answer", "")),
        answered_by=str(d.get("by", "")),
        answered_digest=str(d.get("digest", "")),
        coded=d.get("coded") is True,
        source=str(d.get("source", "")),
        level=_int(d.get("level")),
        code_presented=str(d.get("code_presented", "")),
        role=str(d.get("role", "")),
        via=str(d.get("via", "")),
        human=d.get("human") is True,
        answered_at=ev.ts,
    )


def h_consumed(st: State, ev: Event) -> None:
    """A use of an answered approval was spent by ``by``. Uses beyond the ask's own are not
    counted (a replayed duplicate changes nothing past the cap)."""
    row = asks_of(st).get(_id(ev))
    if row is None or row["state"] != ANSWERED or row["uses"] <= 0 or row["used"] >= row["uses"]:
        return
    row["used"] += 1
    row["consumed"].append(
        {"by": str(ev.data.get("by", "")), "effect": str(ev.data.get("effect", "")), "at": ev.ts}
    )


def _closer(state: str):
    def handler(st: State, ev: Event) -> None:
        row = asks_of(st).get(_id(ev))
        if row is not None and row["state"] == PENDING:
            row.update(state=state, answered_at=ev.ts, answered_by=str(ev.data.get("by", "")))

    return handler


def h_withdrawn(st: State, ev: Event) -> None:
    """The asker took a pending ask back -- or a person REVOKED an answered approval
    (``revoked``): its outcome stays on the row, its state no longer lets anyone spend it."""
    row = asks_of(st).get(_id(ev))
    if row is not None and row["state"] == ANSWERED and ev.data.get("revoked") is True:
        row.update(state=WITHDRAWN, revoked_by=str(ev.data.get("by", "")), revoked_at=ev.ts)
        return
    _closer(WITHDRAWN)(st, ev)


ASK_HANDLERS = {
    "ask.requested": h_requested,
    "ask.answered": h_answered,
    "ask.consumed": h_consumed,
    "ask.withdrawn": h_withdrawn,
    "ask.expired": _closer(EXPIRED),
}
