"""The fold of the token events: which paired tokens were issued and revoked, for the audit.

`token.issued` opens a row and `token.revoked` closes it. Neither carries a secret or a hash:
those stay in the user-level store `services/auth` owns, and that store, not this fold, decides
whether a presented token is good. The fold is pure, so whether a row has EXPIRED is a question
of ``expires_at`` against a clock, answered by `services.auth.state_of`.
"""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State


def tokens_of(st: State) -> dict[str, dict[str, Any]]:
    """The folded tokens, token id -> row. Like `asks_of`: a declared `State` field, and the
    setdefault serves an instance restored without ``__init__``."""
    return st.__dict__.setdefault("tokens", getattr(st, "tokens", {}))


def _id(ev: Event) -> str:
    return str(ev.data.get("id") or ev.subject)


def h_issued(st: State, ev: Event) -> None:
    token_id = _id(ev)
    if not token_id or token_id in tokens_of(st):
        return  # the first issue owns the id; a replayed duplicate changes nothing
    d = ev.data
    tokens_of(st)[token_id] = {
        "id": token_id,
        "name": str(d.get("name", "")),
        "role": str(d.get("role", "")),
        "expires_at": str(d.get("expires_at", "")),
        "by": str(d.get("by", "")),
        "rotated_from": str(d.get("rotated_from", "")),
        "issued_at": ev.ts,
        "revoked_at": "",
        "revoked_by": "",
        "reason": "",
    }


def h_revoked(st: State, ev: Event) -> None:
    row = tokens_of(st).get(_id(ev))
    if row is None or row["revoked_at"]:
        return  # the first revocation stands
    row.update(revoked_at=ev.ts, revoked_by=str(ev.data.get("by", "")))
    row["reason"] = str(ev.data.get("reason", ""))


TOKEN_HANDLERS = {"token.issued": h_issued, "token.revoked": h_revoked}
