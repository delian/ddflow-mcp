"""The fold of the ask events: what the approvals-and-questions engine asked and was answered.

`ask.requested` opens an ask (a question or an approval of ``digest``), `ask.answered` closes
it with an outcome a PERSON gave, `ask.withdrawn` is the asker taking it back and `ask.expired`
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
    """The folded asks, ask id -> row (created on first use)."""
    return st.__dict__.setdefault("asks", {})


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
        "bot_answerable": d.get("bot_answerable") is True,
        "requested_by": ev.agent,
        "requested_at": ev.ts,
        "state": PENDING,
        "outcome": "",
        "answer": "",
        "answered_by": "",
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
        role=str(d.get("role", "")),
        via=str(d.get("via", "")),
        human=d.get("human") is True,
        answered_at=ev.ts,
    )


def _closer(state: str):
    def handler(st: State, ev: Event) -> None:
        row = asks_of(st).get(_id(ev))
        if row is not None and row["state"] == PENDING:
            row.update(state=state, answered_at=ev.ts, answered_by=str(ev.data.get("by", "")))

    return handler


ASK_HANDLERS = {
    "ask.requested": h_requested,
    "ask.answered": h_answered,
    "ask.withdrawn": _closer(WITHDRAWN),
    "ask.expired": _closer(EXPIRED),
}
