"""Fold handlers: gates and reviews: gate outcomes, review triage, out-of-order gates, cadence, reviewers.

Each `_h_*` is a `(State, Event) -> None` fold handler, assembled into `model.HANDLERS`;
the other functions here are the helpers they share. All of it is pure."""

from __future__ import annotations

from ..events import Event
from ..records import GateRecord, State
from ._common import _item


def _count_recording(st: State, gate: str) -> None:
    st.gate_order.setdefault(gate, {"fired": 0, "recorded": 0})["recorded"] += 1


def _h_gate(outcome: str):
    def handler(st: State, ev: Event) -> None:
        d = ev.data
        it = _item(st, ev, d.get("kind", "task"))
        gate = d.get("gate", "")
        if outcome == "started":
            rec = it.gates.setdefault(gate, GateRecord(gate=gate))
            if not rec.outcome:  # a recorded outcome keeps the time it was recorded
                rec.at = ev.ts
        else:
            # The denominator for the order-violation rate. `started` is excluded: it
            # is not a recording, and counting it would deflate the rate by the number
            # of command gates, which are the ones that emit it.
            _count_recording(st, gate)
            it.gates[gate] = GateRecord(
                gate=gate,
                outcome=outcome,
                at=ev.ts,
                by=d.get("by", ev.agent),
                reason=d.get("reason", ""),
                evidence=dict(d.get("evidence", {})),
            )

    return handler


def _h_review_triaged(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    d = ev.data
    if it is None or not d.get("gate") or not d.get("digest"):
        return
    it.triage.setdefault(d["gate"], {})[d["digest"]] = {
        "verdict": d.get("verdict", ""),
        "probe": d.get("probe", ""),
        "n": d.get("finding", 0),
        "severity": d.get("severity", ""),
        "title": d.get("title", ""),
        "location": d.get("location", ""),
        "by": ev.agent,
        "at": ev.ts,
    }


def _h_gate_out_of_order(st: State, ev: Event) -> None:
    """One recording that reached a gate before its predecessors had run.

    Counted rather than merely printed, because `enforce_order`'s default is a
    judgement nobody had evidence for. The denominator lives on the same record: a
    count of violations without a count of recordings is a number that can be made to
    say anything.
    """
    row = st.gate_order.setdefault(ev.data.get("gate", ev.subject), {"fired": 0, "recorded": 0})
    row["fired"] += 1


def _h_cadence(st: State, ev: Event) -> None:
    st.cadences.setdefault(ev.subject, []).append(
        {
            "at": ev.ts,
            "by": ev.agent,
            "result": ev.data.get("result", ""),
            "evidence": ev.data.get("evidence", {}),
        }
    )


def _h_reviewer_configured(st: State, ev: Event) -> None:
    dig = str(ev.data.get("digest", ""))
    if dig:
        st.reviewer_writes[dig] = {
            "name": ev.subject,
            "kind": ev.data.get("kind", ""),
            "agent": ev.agent,
            "person": bool(ev.data.get("person")),
            "user": ev.data.get("user", ""),
            "at": ev.ts,
        }


def _h_reviewer_approved(st: State, ev: Event) -> None:
    dig = str(ev.data.get("digest", ""))
    if dig:
        st.reviewer_approvals[dig] = {
            "name": ev.subject,
            "user": ev.data.get("user", ""),
            "host": ev.data.get("host", ""),
            "note": ev.data.get("note", ""),
            "at": ev.ts,
        }
