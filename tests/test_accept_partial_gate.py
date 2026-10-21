"""Bb999d5e20f: a required gate recorded `partial` (a baseline-red unit_tests, a
`partial_exits` command) could never satisfy `complete`: the only way past was `--force`,
which overrides EVERY blocker. The settle paths are now: re-run the missing part (a new
outcome), or a PERSON accepts that one recorded partial outcome (`ddflow approve <id> <gate>`),
which an agent identity cannot do and which never rewrites the outcome into `passed`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace

import pytest
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP

OK = 0


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


BASELINE = {"baseline": {"red": True, "base": "main", "ids": ["tests/t.py::test_x"]}}


def _partial_item(repo, reason="baseline-red: test_x, bug B1", baseline=True):
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write("\n[agent]\nreviewer_family_must_differ = false\n")
    run_cli(repo, "workflow", "pipeline", "task", "implement,unit_tests,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed")
    code, _o, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "partial", "--reason", reason
    )
    assert code == OK, err
    if baseline:  # what `gate run` measures; a caller's `gate record` cannot write it
        _remeasure(repo, reason)


def _remeasure(repo, reason):
    log = EventLog(repo, "ddflow-gate-run")
    log.append(
        "gate.partial",
        "T1",
        {"gate": "unit_tests", "by": "ddflow", "reason": reason, "evidence": BASELINE},
    )


def test_a_person_can_accept_a_partial_required_gate_and_complete_follows(repo):
    _partial_item(repo)
    code, out, err = run_cli(repo, "complete", "T1")
    assert code != OK and "unit_tests" in out + err, "completed over a partial gate"

    code, out, err = run_cli(repo, "approve", "T1", "unit_tests", "--note", "base bug filed")
    assert code == OK, out + err
    assert _state(repo).items["T1"].gate_outcome("unit_tests") == "partial", (
        "accepting must not turn the partial outcome into a pass"
    )
    code, out, err = run_cli(repo, "complete", "T1")
    assert code == OK, out + err


def test_an_agent_identity_cannot_accept_it(repo):
    _partial_item(repo)
    code, out, err = run_cli(repo, "--agent", "bot", "approve", "T1", "unit_tests")
    assert code != OK and "agent identity" in out + err
    assert run_cli(repo, "complete", "T1")[0] != OK, "an agent accepted its own partial gate"


def test_a_rerecorded_partial_needs_a_fresh_acceptance(repo):
    _partial_item(repo)
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] == OK
    run_cli(repo, "gate", "record", "T1", "unit_tests", "--outcome", "partial", "--reason", "other")
    code = run_cli(repo, "complete", "T1")[0]
    assert code != OK, "an acceptance of an earlier outcome carried over to a later one"


def test_only_a_partial_outcome_can_be_accepted(repo):
    _partial_item(repo)
    run_cli(repo, "gate", "record", "T1", "unit_tests", "--outcome", "unavailable", "--reason", "x")
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] != OK
    assert run_cli(repo, "complete", "T1")[0] != OK


def test_verify_notes_the_acceptance_and_does_not_fail_the_completion(repo):
    _partial_item(repo)
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] == OK
    assert run_cli(repo, "complete", "T1")[0] == OK
    _code, out, err = run_cli(repo, "verify", "T1", "--json")
    assert "required gate(s) not passed" not in out + err, out + err
    assert "accepted by a person" in out + err, out + err


def test_a_partial_not_measured_baseline_red_cannot_be_accepted(repo):
    _partial_item(repo, reason="ran only some tests", baseline=False)
    code, out, err = run_cli(repo, "approve", "T1", "unit_tests")
    assert code != OK and "baseline-red" in out + err
    assert run_cli(repo, "complete", "T1")[0] != OK


def test_a_hand_claimed_baseline_reason_is_not_the_measurement(repo):
    _partial_item(repo, reason="baseline-red: everything, trust me", baseline=False)
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] != OK


def test_a_remeasured_baseline_red_needs_a_fresh_acceptance(repo):
    _partial_item(repo)
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] == OK
    _remeasure(repo, "baseline-red: test_y, bug B2")
    assert run_cli(repo, "complete", "T1")[0] != OK, "the old acceptance carried over"
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] == OK
    assert run_cli(repo, "complete", "T1")[0] == OK


def test_the_evidence_gate_run_writes_is_what_acceptance_reads(repo, monkeypatch):
    """The real producer (`gate_baseline.apply`) nests the measurement as `baseline.red`
    in the gate's evidence; acceptance must read exactly that shape."""
    from ddflow.api import gate_baseline as GB
    from ddflow.core.records import GateRecord
    from ddflow.services import gate_accept as GA
    from ddflow.services.gates import baseline_red as BR

    monkeypatch.setattr(BR, "candidate_ids", lambda *a: ["t.py::test_x"])
    monkeypatch.setattr(BR, "on_base", lambda *a: {"red": True, "base_sha": "abc"})
    monkeypatch.setattr(GB, "_file", lambda *a, **k: "B1")
    ev: dict = {}
    it = SimpleNamespace(id="T1", base="main")
    outcome, why = GB.apply(
        repo,
        None,
        "a",
        it,
        "unit_tests",
        SimpleNamespace(command="pytest"),
        "failed",
        ev,
        None,
        False,
    )
    assert outcome == "partial"
    task = SimpleNamespace(
        id="T1",
        gates={"unit_tests": GateRecord("unit_tests", "partial", "t", "a", why, ev)},
        gate_outcome=lambda g: "partial",
    )
    assert GA.refusal(task, "unit_tests") == ""


def test_a_gate_the_diff_made_not_applicable_gets_no_acceptance_warning(repo):
    from ddflow.services import completion as CM

    _partial_item(repo)
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] == OK
    s = SimpleNamespace(pipeline=["unit_tests"], not_applicable={"unit_tests": "docs only"})
    st = _state(repo)
    assert CM._accepted_warnings(st, st.items["T1"], s, {"unit_tests"}, []) == []


def test_a_caller_cannot_forge_the_measurement_through_gate_record(repo):
    _partial_item(repo, reason="baseline-red: forged", baseline=False)
    run_cli(
        repo,
        "gate", "record", "T1", "unit_tests", "--outcome", "partial", "--reason", "baseline-red: x",
        "--evidence", '{"baseline": {"red": true}}',
    )  # fmt: skip
    assert run_cli(repo, "approve", "T1", "unit_tests")[0] != OK


def test_a_partial_is_never_a_chore_skip(repo):
    """The acceptance warning cannot be mistaken for a chore excuse: `skipped_ok` needs a
    `skipped` outcome, which a partial never is."""
    from ddflow.services import chore as CO

    _partial_item(repo)
    st = _state(repo)
    cfg = __import__("ddflow.config", fromlist=["Config"]).Config.load(repo)
    assert not CO.skipped_ok(cfg, st.items["T1"], object(), "unit_tests")
