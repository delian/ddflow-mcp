"""D-gate-economy 2 and D-unify 5: once a review gate's round budget is spent, the triage
that gives its last finding a verdict records the gate passed ON REFUTATION -- flagged
in its evidence and in gate status -- and a finding left without one holds the gate and
says to settle it or ask the operator (B-gate-econ-review-refutation.1-record).
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

OK = 0


@pytest.fixture(autouse=True)
def _full_rounds(monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_DELTA_DEFAULT", "0")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _setup(repo: Path, tmp_path: Path, findings: int = 1) -> None:
    """A command reviewer that reports ``findings`` findings on every round."""
    cli = tmp_path / "fake-reviewer"
    body = "".join(f"FINDING HIGH x.py:{n}\\nproblem {n}\\n\\n" for n in range(1, findings + 1))
    cli.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '{body}STATUS: FINDINGS {findings}\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1\n")


def _gate(repo: Path, gate: str = "critic"):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


def _status_line(repo: Path, gate: str = "critic") -> str:
    st = fold(EventLog(repo).read_all(), strict=False)
    return G.status(st, Config.load(repo), "T1").triage.get(gate, "")


def test_settling_the_last_finding_after_the_cap_passes_on_refutation(repo, tmp_path):
    _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it: ok")
    assert out.exit == OK, out.reason
    assert out.data["passed_on_refutation"] is True
    assert "passed ON REFUTATION (flagged)" in out.data["text"]
    rec = _gate(repo)
    assert rec.outcome == "passed"
    assert rec.evidence["passed_on_refutation"] == {
        "refuted": 1,
        "confirmed": 0,
        "rounds": 2,
        "max_rounds": 2,
    }
    assert rec.by == "gemini-2.5-pro", "the reviewer stays the one who reviewed"
    assert rec.evidence["chunk_findings"], "the review's findings stay on the record"
    assert _status_line(repo).endswith("-- PASSED ON REFUTATION")


def test_before_the_cap_a_triage_leaves_the_gate_to_a_re_review(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed"
    assert "PASSED ON REFUTATION" not in _status_line(repo)


def test_an_unsettled_finding_after_the_cap_holds_the_gate_and_names_the_operator(repo, tmp_path):
    _setup(repo, tmp_path, findings=2)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it")
    assert out.data["passed_on_refutation"] is False
    assert "1 finding(s) have no verdict" in out.data["text"]
    assert "--force --reason" in out.data["text"] and "operator" in out.data["text"]
    assert _gate(repo).outcome == "failed"
    out = api.triage(repo, "T1", gate="critic", finding=2, verdict="confirmed", probe="t passes")
    assert out.data["passed_on_refutation"] is True
    flag = _gate(repo).evidence["passed_on_refutation"]
    assert (flag["refuted"], flag["confirmed"]) == (1, 1)


def test_findings_all_confirmed_and_fixed_pass_without_the_refutation_flag(repo, tmp_path):
    """B1396d7bd55: D-unify 5 flags gates whose findings were REFUTED with probes. A gate
    whose every finding was confirmed and fixed is a fix, not a refutation: passed, shown
    as fixed, and absent from `gate list --refuted`."""
    _setup(repo, tmp_path)
    for _ in (1, 2):
        api.review(repo, gate="critic", item="T1")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert "ON REFUTATION" not in out.data["text"]
    rec = _gate(repo)
    assert rec.outcome == "passed"
    assert "passed_on_refutation" not in rec.evidence
    assert rec.evidence["findings_fixed"] == {"confirmed": 1, "rounds": 2, "max_rounds": 2}
    st = fold(EventLog(repo).read_all(), strict=False)
    assert G.on_refutation(st.items["T1"], "critic") is None
    assert G.refuted_passes(st) == []
    assert _status_line(repo).endswith("-- findings fixed")
    assert "ON REFUTATION" not in _status_line(repo)


def test_an_unlimited_budget_never_passes_on_refutation(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_MAX_ROUNDS", "0")
    _setup(repo, tmp_path)
    for _ in (1, 2, 3):
        api.review(repo, gate="critic", item="T1")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it")
    assert out.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed"


def test_a_clean_review_pass_is_not_flagged(repo, tmp_path):
    _setup(repo, tmp_path, findings=0)
    assert api.review(repo, gate="critic", item="T1").data["outcome"] == "passed"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert G.on_refutation(st.items["T1"], "critic") is None


def test_the_refutation_pass_is_not_a_review_round(repo, tmp_path):
    """roborev on 1e5dab6e: the pass copies the review's evidence, review_kind included,
    and was counted as a third round."""
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1")
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it")
    assert _gate(repo).outcome == "passed"
    assert api._rounds_used(EventLog(repo), "T1", "critic") == 2
    out = api.review(repo, gate="critic", item="T1")
    assert "has had 2 review rounds" in out.reason, out.reason
