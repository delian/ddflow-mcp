"""D-unify 5: a gate passed on refutation is visible beyond `gate status` -- in `status`, the
brief, the completion result -- and `ddflow gate list --refuted` lists them for the operator
(B-gate-econ-review-refutation.2-surfaces).
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli
from test_gate_econ_review_refutation import _setup

import ddflow.api as A
import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G


@pytest.fixture(autouse=True)
def _full_rounds(monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_DELTA_DEFAULT", "0")


def _flag(repo, tmp_path):
    """T1's critic gate passed on refutation (budget spent, last finding refuted)."""
    _setup(repo, tmp_path)
    for _ in (1, 2):
        api.review(repo, gate="critic", item="T1")
    assert api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ok").exit == 0


def test_the_service_lists_only_flagged_passes_with_their_counts(repo, tmp_path):
    _flag(repo, tmp_path)
    st = fold(EventLog(repo).read_all(), strict=False)
    [row] = G.refuted_passes(st)
    assert (row["item"], row["gate"], row["refuted"], row["confirmed"]) == ("T1", "critic", 1, 0)
    assert G.refuted_passes(st, ["nope"]) == []
    assert G.refuted_line(row).startswith("T1.critic  1 refuted, 0 confirmed, 2 round(s)")


def test_gate_list_refuted_on_the_cli_and_in_json(repo, tmp_path):
    _flag(repo, tmp_path)
    code, out, _err = run_cli(repo, "gate", "list", "--refuted")
    assert code == 0 and out.startswith("T1.critic  1 refuted")
    code, out, _err = run_cli(repo, "--json", "gate", "list", "--refuted")
    body = json.loads(out)
    assert body["count"] == 1 and body["passes"][0]["gate"] == "critic"


def test_gate_list_refuted_says_so_when_there_is_none(repo):
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "gate", "list", "--refuted")
    assert code == 0 and "no gate was passed on refutation" in out


def test_gate_list_without_the_flag_lists_the_defined_gates(repo):
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "gate", "list")
    assert code == 0 and "critic" in out and "unit_tests" in out


def test_the_mcp_tool_takes_refuted(repo, tmp_path):
    _flag(repo, tmp_path)
    out = A.gate_list(repo, refuted=True)
    assert out.exit == 0 and out.data["count"] == 1
    assert A.gate_list(repo).data["refuted"] is False


def test_status_counts_them_and_stays_quiet_when_there_are_none(repo, tmp_path):
    _setup(repo, tmp_path)
    assert "refuted_passes" not in A.status(repo).data
    _, text, _ = run_cli(repo, "status")
    assert "passed on refutation" not in text
    for _ in (1, 2):
        api.review(repo, gate="critic", item="T1")
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ok")
    data = A.status(repo).data
    assert data["refuted_passes"]["gates"] == 1 and data["refuted_passes"]["items"] == 1
    _, text, _ = run_cli(repo, "status")
    assert "1 gate(s) passed on refutation (`ddflow gate list --refuted`)" in text


def test_the_brief_names_an_unfinished_items_flagged_gate(repo, tmp_path):
    _setup(repo, tmp_path)
    assert "Passed on refutation" not in A.brief(repo).data["text"]
    for _ in (1, 2):
        api.review(repo, gate="critic", item="T1")
    api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ok")
    text = A.brief(repo).data["text"]
    assert "Passed on refutation, not yet completed: T1.critic" in text
    assert "ddflow gate list --refuted" in text


def test_the_completion_result_names_the_gates_passed_on_refutation(repo, tmp_path):
    from conftest import pass_pipeline

    _flag(repo, tmp_path)
    pass_pipeline(repo, "T1", omit=("critic",))
    run_cli(repo, "claim", "T1", "--no-worktree")
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5-5")
    assert code == 0, err
    assert "PASSED ON REFUTATION: T1.critic  1 refuted" in out
