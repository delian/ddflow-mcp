"""The gated verb `gate.skip.failed_review`: its canonical command and state digest.

Setting aside a FAILED review gate (a skip, or a hand-recorded pass/unavailable/partial) is the
operator's override (D-failed-review-gate). An agent may do it with a person's approval of
exactly this: this verb, this item and gate, this outcome, and the gate's failed state as it
is now (its latest outcome, why it is unresolved, every finding's verdict, how many rounds
ran). A new review round, a new triage or a changed outcome is a different state and needs a
new approval. `provider` is the state provider `ddflow approvals request` uses (registered in services/approvals/state.py)."""

from __future__ import annotations

import json

from ...core.digest import content_digest
from .canon import Canon

VERB = "gate.skip.failed_review"


def state_digest(it, gate: str, why: str) -> str:
    """The digest of what the override would set aside."""
    triage = {d: v.get("verdict", "") for d, v in sorted((it.triage.get(gate) or {}).items())}
    record = {
        "outcome": it.gate_outcome(gate),
        "why": why,
        "rounds": len(it.runs_of(gate)),
        "triage": triage,
    }
    return content_digest(json.dumps(record, sort_keys=True, separators=(",", ":")))


def canon_for(it, gate: str, item: str, why: str, agent: str, outcome: str) -> Canon:
    return Canon(
        verb=VERB,
        agent=agent,
        item=item,
        target=gate,
        # a plain skip carries no argument, so a request filed by hand (`approvals request`) is
        # the same command; any other outcome being hand-recorded is part of what is approved
        args={} if outcome in ("", "skipped") else {"outcome": outcome},
        state=state_digest(it, gate, why),
    )


def provider(st, item: str, gate: str, args: dict[str, str]) -> str:
    from ..gates import failed_review as GO

    it = st.items.get(item)
    if it is None:
        raise ValueError(f"no such item {item!r}")
    return state_digest(it, gate, GO.unresolved_review_failure(it, gate))
