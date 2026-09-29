"""Every phase reviews and updates its documentation before it merges.

Operator, 2026-09-28: "Make sure in the ddflow workflow by default we have at least one
per phase review and update of the documentation to make sure the documentation and
README are always up to date". The per-commit stale-docs check catches a renamed or
removed name; nothing asked whether a NEW command, knob or behaviour was written down.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.services import gates as G
from ddflow.services import workflow as WF

REFUSED = 3


def test_the_default_phase_pipeline_reviews_the_docs_right_before_it_merges():
    pipe = Config().gates.phase_pipeline
    assert pipe[-2:] == ["docs", "merge"]
    assert "docs" in Config().gates.evidence_required, "an unexplained pass is not evidence"
    gate = G.DEFAULT_GATES["docs"]
    assert gate.applies_to == "phase" and gate.evidence
    assert "README" in gate.prompt and "no user-visible change" in gate.prompt


def test_a_phase_cannot_complete_with_its_docs_unreviewed(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "a phase")
    code, out, err = run_cli(repo, "complete", "P1")
    assert code == REFUSED
    assert "docs" in out + err


def test_a_docs_pass_is_recorded_with_what_was_updated(repo):
    """Refusing a BARE pass is the evidence contract's job, which is broken for every
    evidence-required gate today (filed: B-evidence-contract); this pins that the gate is
    on that list and that a pass naming the files is recorded."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "a phase")
    code, _out, err = run_cli(
        repo, "gate", "record", "P1", "docs", "--outcome", "passed",
        "--evidence", "README: documented `ddflow tests`",
    )  # fmt: skip
    assert code == 0, err
    code, out, _ = run_cli(repo, "gate", "status", "P1")
    assert "[x] docs" in out, out


def test_a_custom_phase_pipeline_without_docs_is_advised_against():
    cfg = Config()
    assert not [f for f in WF.check(cfg, G.DEFAULT_GATES) if f.subject == "phase_pipeline"]
    cfg.gates.phase_pipeline = [g for g in cfg.gates.phase_pipeline if g != "docs"]
    found = [f for f in WF.check(cfg, G.DEFAULT_GATES) if f.subject == "phase_pipeline"]
    assert [f.level for f in found] == [WF.ADVISORY] and "'docs' gate" in found[0].detail
    # Present but after the merge is the same failure (LAN DeepSeek on B-docs-phase-gate).
    cfg.gates.phase_pipeline = ["unit_tests", "merge", "docs"]
    found = [f for f in WF.check(cfg, G.DEFAULT_GATES) if f.subject == "phase_pipeline"]
    assert len(found) == 1 and "after 'merge'" in found[0].detail


def test_the_driver_tells_the_agent_to_record_it():
    driver = (
        Path(__file__).resolve().parents[1] / "ddflow/templates/drivers/implement-phase.md"
    ).read_text()
    assert "ddflow gate record <NAME> docs" in driver
