"""Every descriptor answers every catalog row (PAR-answers): the per-row answer reuses the
facts three-valued states, derived from what the descriptor declares, never guessed.
"""

from __future__ import annotations

from ddflow.services import facts
from ddflow.services import harnessreg as HR
from ddflow.services.parity import answers as A
from ddflow.services.parity import catalog as CAT


def test_every_descriptor_answers_every_row():
    rows = CAT.load().rows
    for h in HR.load():
        got = A.answers(h)
        assert set(got) == {r.feature for r in rows}, (h.id, "an unanswered or extra row")
        assert A.unanswered(h) == []


def test_an_answer_is_a_facts_status_not_a_second_state_machine():
    h = HR.get("grok")
    assert set(A.answers(h).values()) <= set(facts.STATUSES)


def test_a_row_with_no_descriptor_field_is_not_applicable():
    # lean-tool-profile / key-by-env-name are ddflow-side knobs with no descriptor field
    h = HR.get("grok")
    got = A.answers(h)
    assert got["lean-tool-profile"] == "none"
    assert got["key-by-env-name"] == "none"


def test_a_declared_section_reads_documented_and_absent_reads_unverified():
    h = HR.get("grok")
    got = A.answers(h)
    # grok declares [mcp] (documented, never over-claimed verified); it has no [capabilities]
    assert got["mcp-config"] == "documented"
    assert got["model-visibility"] in ("none", "unverified")


def test_the_answer_only_depends_on_the_descriptor_and_catalog():
    # the derivation is total: for a synthetic absence every field resolves, never raises
    rows = CAT.load().rows
    for row in rows:
        assert A.answer(HR.get("grok"), row) in facts.STATUSES
