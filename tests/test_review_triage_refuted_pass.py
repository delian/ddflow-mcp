"""The triage that refutes a failed round's last finding passes the gate, once, with evidence.

Agents used to refute every finding and then re-record the gate `passed` by hand
(DX-triage-refuted-gate-pass): the gate stayed `failed` until the round budget was spent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_triage import _full_review, _setup, _state

import ddflow.api.review_settle as ST
from ddflow.infra.log import EventLog

OK = 0


def _tri(repo, n, verdict="refuted", probe="ran it: no such bug"):
    return ST.triage(repo, "T1", gate="critic", finding=n, verdict=verdict, probe=probe)


def _passed_events(repo) -> int:
    return [e.kind for e in EventLog(repo).read_all()].count("gate.passed")


def test_refuting_every_finding_passes_the_gate_with_the_probes_as_evidence(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    first = _tri(repo, 1, probe="probe one")
    assert first.exit == OK and _state(repo).gates["critic"].outcome == "failed"
    assert "ON REFUTATION" not in first.data["text"], "one finding is still open"
    last = _tri(repo, 2, probe="probe two")
    assert last.exit == OK and last.data["passed_on_refutation"] is True
    assert "passed ON REFUTATION" in last.data["text"]
    gate = _state(repo).gates["critic"]
    assert gate.outcome == "passed"
    flag = gate.evidence["passed_on_refutation"]
    assert flag["refuted"] == 2 and flag["confirmed"] == 0
    assert [r["probe"] for r in flag["refutations"]] == ["probe one", "probe two"]
    assert _passed_events(repo) == 1
    code, out, _err = run_cli(repo, "gate", "list", "--refuted")
    assert code == OK and "T1" in out, out


def test_a_confirmed_finding_keeps_the_gate_failed(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1)
    done = _tri(repo, 2, "confirmed", "fixed in abc, tests/test_x.py::t")
    assert "ON REFUTATION" not in done.data["text"]
    assert _state(repo).gates["critic"].outcome == "failed"


def test_a_second_triage_after_the_pass_does_not_pass_it_again(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1)
    _tri(repo, 2)
    again = _tri(repo, 2, probe="a better probe")
    assert again.exit == OK and "ON REFUTATION" not in again.data["text"]
    assert _passed_events(repo) == 1


def test_the_knob_turns_it_off(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "config", "--set", "review.settle_all_refuted", "false")
    _full_review(repo)
    _tri(repo, 1)
    out = _tri(repo, 2)
    assert "ON REFUTATION" not in out.data["text"]
    assert _state(repo).gates["critic"].outcome == "failed"


def test_the_cli_triage_says_so(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    for n in (1, 2):
        code, out, err = run_cli(
            repo, "review", "triage", "T1", "--gate", "critic", "--finding", str(n),
            "--refuted", "--probe", "ran it",
        )  # fmt: skip
        assert code == OK, err
    assert "passed ON REFUTATION" in out, out


def test_a_confirmed_first_finding_keeps_the_gate_failed_whatever_the_order(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1, "confirmed", "fixed in abc, tests/test_x.py::t")
    last = _tri(repo, 2)
    assert "ON REFUTATION" not in last.data["text"]
    assert _state(repo).gates["critic"].outcome == "failed"


def test_complete_does_not_name_the_refuted_gate_as_unmet(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1)
    _tri(repo, 2)
    assert _state(repo).gates["critic"].outcome == "passed"
    code, out, err = run_cli(repo, "complete", "T1")
    unmet = [ln for ln in (out + err).splitlines() if ln.lstrip().startswith("- ")]
    assert code != OK or not unmet  # other gates of T1 are unrun: the point is the line below
    assert not any("critic" in ln for ln in unmet), unmet


def test_before_the_cap_it_passes_too_and_the_evidence_is_this_rounds(repo, tmp_path):
    _setup(repo, tmp_path)
    run_cli(repo, "config", "--set", "review.max_rounds", "2")
    _full_review(repo)
    _tri(repo, 1, probe="p1")
    _tri(repo, 2, probe="p2")
    flag = _state(repo).gates["critic"].evidence["passed_on_refutation"]
    assert flag["rounds"] == 1 and flag["max_rounds"] == 2
    assert [(r["finding"], r["probe"]) for r in flag["refutations"]] == [(1, "p1"), (2, "p2")]
