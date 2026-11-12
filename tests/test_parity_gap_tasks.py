"""The gap-task plan (PAR-gap-tasks): per agent the catalog rows it has not answered, one
task per gap, deduped by (agent, row), needs descriptor-first.
"""

from __future__ import annotations

from ddflow.services import harnessreg as HR
from ddflow.services.parity import gap_tasks as GT


def test_gaps_are_the_unverified_rows_deduped_by_agent_row():
    hs = HR.load()
    gaps = GT.rows(hs)
    keys = {(g.agent, g.feature) for g in gaps}
    assert len(keys) == len(gaps), "a gap appears once per (agent, row)"
    # only the unverified rows became gaps; nothing graded otherwise leaks in
    for h in hs:
        got = set(GT.by_agent(hs)[h.id])
        assert not got - {g.feature for g in gaps if g.agent == h.id}


def test_a_gap_phase_is_named_for_the_agent():
    assert GT.phase("grok") == "P-grok"


def test_the_filing_order_is_descriptor_first():
    assert GT.DESCRIPTOR_FIRST[:2] == ("descriptor", "probe")
    assert GT.DESCRIPTOR_FIRST[-1] == "parity"
    assert len(set(GT.DESCRIPTOR_FIRST)) == len(GT.DESCRIPTOR_FIRST), "one name each"
