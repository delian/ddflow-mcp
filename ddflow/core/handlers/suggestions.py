"""The fold of the suggestion events: a bounded change one agent proposes to the holder of a file.

`suggestion.proposed` opens a record (D-handoff-peers). The rest move it, and WHO may move it
is decided here from the event's own author, so a hand-appended line from the wrong agent
changes nothing:

* the OWNER (``to_agent``) accepts, applies, rejects (a reason is required) and counters;
* the SENDER (``from_agent``) withdraws and supersedes;
* DDFLOW (``role`` "ddflow") marks seen, expired, and applied-by-patch-id;
* the OPERATOR (``role`` "operator", which the service grants only to a person) may do any.

The ``role`` marks ("ddflow", "operator") are the log's word, as an ask's source is: the fold
cannot read the actor table, so `services.handoff.events.move` grants them (only to ddflow's own
sweep, or to an actor holding ``approve``) and a same-user process that hand-writes one is as
trusted as one that hand-writes an `ask.answered`. No move ever applies a patch by itself.

The fold is pure: whether a suggestion has EXPIRED is a question of a clock, answered by
`services.handoff`, never here -- replaying a log at any time yields the same rows. Patch text
is never in the row: only the digest of the stored (redacted) patch, the files and the counts.
"""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State

OPEN, SEEN, ACCEPTED = "open", "seen", "accepted"
APPLIED, REJECTED, COUNTERED = "applied", "rejected", "countered"
WITHDRAWN, SUPERSEDED, EXPIRED = "withdrawn", "superseded", "expired"
#: States in which the suggestion still stands between sender and owner.
LIVE = (OPEN, SEEN, ACCEPTED)
#: States in which the owner has not yet answered (owner merge and complete refuse on these).
UNANSWERED = (OPEN, SEEN)

OWNER, SENDER, DDFLOW, OPERATOR = "owner", "sender", "ddflow", "operator"
#: new state -> (states it may move from, who may move it, whether a reason is required).
MOVES: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    SEEN: ((OPEN,), (DDFLOW,), False),
    ACCEPTED: ((OPEN, SEEN), (OWNER,), False),
    APPLIED: ((OPEN, SEEN, ACCEPTED), (OWNER, DDFLOW), False),
    REJECTED: ((OPEN, SEEN), (OWNER,), True),
    COUNTERED: ((OPEN, SEEN), (OWNER,), True),
    WITHDRAWN: ((OPEN, SEEN, ACCEPTED), (SENDER,), False),
    SUPERSEDED: ((OPEN, SEEN, ACCEPTED), (SENDER,), False),
    EXPIRED: ((OPEN, SEEN), (DDFLOW,), False),
}


def suggestions_of(st: State) -> dict[str, dict[str, Any]]:
    """The folded suggestions, id -> row; every reader goes through here."""
    return st.__dict__.setdefault("suggestions", getattr(st, "suggestions", {}))


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _id(ev: Event) -> str:
    return str(ev.data.get("id") or ev.subject)


def _strs(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else []


def h_proposed(st: State, ev: Event) -> None:
    d = ev.data
    sid = _id(ev)
    if not sid or sid in suggestions_of(st):
        return  # the first proposal owns the id; a replayed duplicate changes nothing
    suggestions_of(st)[sid] = {
        "id": sid,
        "from_item": str(d.get("from_item", "")),
        "from_agent": str(d.get("from_agent") or ev.agent),
        "to_item": str(d.get("to_item", "")),
        "to_agent": str(d.get("to_agent", "")),
        "base": str(d.get("base", "")),
        "ref": str(d.get("ref", "")),
        "patch_id": str(d.get("patch_id", "")),
        "blob": str(d.get("blob", "")),
        "digest": str(d.get("digest", "")),
        "files": _strs(d.get("files")),
        "added": _int(d.get("added")),
        "removed": _int(d.get("removed")),
        "kind": str(d.get("kind", "")),
        "reason": str(d.get("reason", "")),
        "probe": str(d.get("probe", "")),
        "evidence": str(d.get("evidence", "")),
        "state": OPEN,
        "proposed_at": ev.ts,
        "history": [],
    }


def _role(row: dict[str, Any], ev: Event) -> str:
    """Who this event's author is to this suggestion; "" when nobody who may move it."""
    claimed = str(ev.data.get("role", ""))
    if claimed in (DDFLOW, OPERATOR):
        return claimed
    if ev.agent and ev.agent == row["to_agent"]:
        return OWNER
    if ev.agent and ev.agent == row["from_agent"]:
        return SENDER
    return ""


def _mover(state: str):
    def handler(st: State, ev: Event) -> None:
        row = suggestions_of(st).get(_id(ev))
        if row is None:
            return
        froms, who, needs_reason = MOVES[state]
        role = _role(row, ev)
        raw = ev.data.get("reason", "")
        reason = raw if isinstance(raw, str) else ""
        if row["state"] not in froms or role not in (*who, OPERATOR):
            return  # not from this state, or not this author's move: nothing changes
        if needs_reason and not reason.strip():
            return  # a rejection or a counter without its reason is not an answer
        row["state"] = state
        row["history"].append(
            {"state": state, "by": ev.agent, "role": role, "reason": reason, "at": ev.ts}
        )
        if state == SUPERSEDED:
            row["superseded_by"] = str(ev.data.get("by_id", ""))
        if state == COUNTERED:
            row["counter"] = str(ev.data.get("counter", ""))

    return handler


SUGGESTION_HANDLERS = {
    "suggestion.proposed": h_proposed,
    **{f"suggestion.{state}": _mover(state) for state in MOVES},
}
