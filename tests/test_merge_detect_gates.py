"""The workflow-path merge-candidate detector (WP-merge-detect-gates)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.records.items import Item
from ddflow.services.metrics.merge_gates import (
    COVERED_BY,
    gate_merge_candidates,
    has_figures,
    text,
)

FULL = [
    "research",
    "rules",
    "implement",
    "rubber_duck",
    "critic",
    "standards",
    "ci",
    "unit_tests",
    "bug_hunt",
    "dedupe",
    "merge",
]


def cfg_with(pipeline: list[str]) -> Config:
    cfg = Config()
    cfg.gates.task_pipeline = list(pipeline)
    return cfg


def task() -> Item:
    return Item("t1", "task")


def test_proposes_run_and_record_groups_for_a_task_pipeline():
    data = gate_merge_candidates(None, cfg_with(FULL), task())
    assert data["run_together"] == [
        {"gates": ["rubber_duck", "critic"], "input": "review-diff"}
    ]
    record = {tuple(r["gates"]): r for r in data["record_together"]}
    assert tuple(record) == (("research", "rules", "implement"), ("bug_hunt", "dedupe"))
    # the review pair and the bookkeeping groups all carry ONE input class: the falsifier
    # (a proposal that includes a gate whose inputs differ) cannot arise from these groups
    assert all(r["input"] == "review-diff" for r in data["run_together"])
    assert all(r["input"] == "none" for r in data["record_together"])


def test_covered_by_is_proposed_only_when_ci_and_unit_tests_are_both_present():
    data = gate_merge_candidates(None, cfg_with(FULL), task())
    assert data["covered_by"] == [{"gate": "unit_tests", "by": "ci", "input": "tree"}]
    no_ci = [g for g in FULL if g != "ci"]
    assert gate_merge_candidates(None, cfg_with(no_ci), task())["covered_by"] == []


def test_a_partial_group_is_not_proposed():
    # research dropped, so research+rules+implement is partial -> not proposed
    pipe = [g for g in FULL if g != "research"]
    record = gate_merge_candidates(None, cfg_with(pipe), task())["record_together"]
    assert all(tuple(r["gates"]) != ("research", "rules", "implement") for r in record)


def test_no_merge_when_the_pipeline_is_empty():
    data = gate_merge_candidates(None, cfg_with([]), task())
    assert has_figures(data) is False


def test_covered_by_maps_each_covered_gate_to_one_coverer():
    assert COVERED_BY.get("unit_tests") == "ci"


def test_covered_by_is_not_proposed_when_the_coverer_comes_after_the_covered_gate():
    # ci AFTER unit_tests cannot have taken it on this tree -> no covered_by
    reordered = ["unit_tests", "ci"]
    data = gate_merge_candidates(None, cfg_with(reordered), task())
    assert data["covered_by"] == []


def test_a_bookkeeping_member_that_needs_its_own_evidence_is_dropped():
    cfg = cfg_with(FULL)
    cfg.gates.evidence_required = ["implement"]
    record = gate_merge_candidates(None, cfg, task())["record_together"]
    assert all(tuple(r["gates"]) != ("research", "rules", "implement") for r in record)
    assert ("bug_hunt", "dedupe") in {tuple(r["gates"]) for r in record}


def test_text_renders_proposals_and_names_the_next_call():
    out = text(gate_merge_candidates(None, cfg_with(FULL), task()))
    assert "run together: rubber_duck, critic" in out
    assert "record together: research, rules, implement" in out
    assert "next: ddflow gates merge  |  ddflow_gate" in out
