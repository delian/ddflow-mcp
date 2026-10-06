"""The fold of the approval events into `State.approvals` (`services.approval`).

`approval.granted` -- a PERSON approved ``subject`` as it was at ``digest`` -- and
`approval.used` -- a single-use approval's token was spent. A reviewer approval
(``reviewer:<name>``) also feeds `State.reviewer_approvals`, which the reviewer-trust
checks read; the older `reviewer.approved` event feeds both tables the same way
(`gates._h_reviewer_approved`), so a log of either era answers both readers alike.
"""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State

#: The subject prefix of a reviewer approval: ``reviewer:<name>``.
REVIEWER = "reviewer:"


def grant_row(ev: Event, subject: str) -> dict[str, Any]:
    """One approval as `State.approvals` keeps it."""
    d = ev.data
    return {
        "subject": subject,
        "digest": str(d.get("digest", "")),
        "user": d.get("user", ""),
        "host": d.get("host", ""),
        "human": bool(d.get("human")),
        "note": d.get("note", ""),
        "token_hash": d.get("token_hash", ""),
        "used_at": "",
        "agent": ev.agent,
        "at": ev.ts,
    }


def h_granted(st: State, ev: Event) -> None:
    subject = str(ev.data.get("subject") or ev.subject)
    row = grant_row(ev, subject)
    if not row["digest"]:
        return
    st.approvals.setdefault(subject, []).append(row)
    # Only a grant that says a person made it: `agent_marker` is the gate at grant time,
    # and like every human checkpoint this makes a forgery visible, not impossible.
    if subject.startswith(REVIEWER) and row["human"]:
        st.reviewer_approvals[row["digest"]] = {
            "name": subject[len(REVIEWER) :],
            "user": row["user"],
            "host": row["host"],
            "note": row["note"],
            "at": row["at"],
        }


def h_used(st: State, ev: Event) -> None:
    """A single-use approval spent: the first unspent grant with that token."""
    subject = str(ev.data.get("subject") or ev.subject)
    token_hash = str(ev.data.get("token_hash", ""))
    for row in st.approvals.get(subject, []):
        if token_hash and row["token_hash"] == token_hash and not row["used_at"]:
            row["used_at"] = ev.ts
            return


#: event kind -> its handler, for `model.HANDLERS`.
APPROVAL_HANDLERS = {"approval.granted": h_granted, "approval.used": h_used}
