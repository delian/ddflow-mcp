"""The operator queue: a pure function of State, Config, `now` and Facts (OD-queue).

``build`` runs every collector named in the source table and returns the entries ranked by
severity, then impact, then age. Same inputs, same entries, same order. An entry never
disappears by being displayed: it leaves when the fact that made it true stops being true.

Kinds nothing can detect yet are listed in `UNCOLLECTED` and reported as ``gaps``, so an empty
queue is never mistaken for a clean one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import collect_asks as _asks
from . import collect_work as _work
from .entry import Entry, Facts, rank
from .sources import OPERATOR, SOURCES

# The names `sources.Source.collector` promises; each is a pure collector.
collect_approval = _asks.collect_approval
collect_gate_skip = _asks.collect_gate_skip
collect_gate_partial = _asks.collect_gate_partial
collect_force_completion = _asks.collect_force_completion
collect_chore = _asks.collect_chore
collect_reviewer = _asks.collect_reviewer
collect_export_ack = _asks.collect_export_ack
collect_plan = _asks.collect_plan
collect_question = _asks.collect_question
collect_decide = _work.collect_decide
collect_phase_close = _work.collect_phase_close
collect_decision = _work.collect_decision
collect_gate_spotcheck = _work.collect_gate_spotcheck
collect_credential = _work.collect_credential
collect_red_main = _work.collect_red_main
collect_lease = _work.collect_lease
collect_agent_failed = _work.collect_agent_failed

#: kind -> why it cannot be detected from the folded state today.
UNCOLLECTED: dict[str, str] = {
    "agent_failed": "no run's failure text (rate or spend limit, crash) is recorded in the log yet",
}

#: Said once with every queue: what is only seen after an agent files the request.
ASK_NOTE = (
    "gate skips, partial gates, forced completions, chores and reviewers appear once the agent "
    "files the request (a refusal does it when [approvals].auto_request is on)"
)


@dataclass(frozen=True)
class Queue:
    entries: tuple[Entry, ...]
    #: Kinds not collected, with the reason; and what a caller did not supply.
    gaps: tuple[str, ...] = field(default=())
    now: float = 0.0

    def for_operator(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.owner == OPERATOR)


def _order(e: Entry):
    return (-rank(e.severity), -e.impact, -e.age_s, e.id)


def build(st, cfg, now: float, facts: Facts | None = None) -> Queue:
    facts = facts or Facts()
    found: list[Entry] = []
    for src in SOURCES:
        found.extend(globals()[f"collect_{src.kind}"](st, cfg, now, facts))
    gaps = [f"{k}: {why}" for k, why in sorted(UNCOLLECTED.items())] + [ASK_NOTE]
    if facts.health is None:
        gaps.append("lease: health was not checked (stalled and dead leases are not listed)")
    return Queue(tuple(sorted(found, key=_order)), tuple(gaps), now)
