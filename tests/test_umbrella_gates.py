"""An umbrella's gates carry an outcome when it closes (bug Bd37a921536).

Closing the last sub-task completes the umbrella; its own gates used to stay silent.
They are now recorded `skipped`, rolled up from the sub-tasks, naming them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0


def _split(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P", "--title", "phase")
    run_cli(repo, "task", "add", "P.T", "--title", "big", "--globs", "a.py")
    code, out, err = run_cli(repo, "split", "P.T", "--into", "P.T.a=one", "--into", "P.T.b=two")
    assert code == OK, out + err


def _umbrella_gate_events(repo: Path) -> list:
    return [
        e for e in EventLog(repo).read_all() if e.subject == "P.T" and e.kind.startswith("gate.")
    ]


def test_closing_the_last_child_gives_every_umbrella_gate_an_outcome(repo):
    _split(repo)
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert run_cli(repo, "complete", "P.T.b", "--force")[0] == OK
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["P.T"].state == "done"
    evs = _umbrella_gate_events(repo)
    assert evs, "the umbrella completed with its gates silent"
    code, out, err = run_cli(repo, "--json", "gate", "status", "P.T")
    assert code == OK, out + err
    assert {e.data["gate"] for e in evs} >= {"research", "implement", "unit_tests", "merge"}
    for e in evs:
        assert e.kind == "gate.skipped"
        assert "P.T.a" in e.data["reason"] and "P.T.b" in e.data["reason"]
        assert e.data["evidence"]["rolled_up_from"] == ["P.T.a", "P.T.b"]


def test_an_outcome_the_umbrella_already_has_is_kept(repo):
    _split(repo)
    assert run_cli(repo, "gate", "skip", "P.T", "research", "--reason", "mine")[0] == OK
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert run_cli(repo, "complete", "P.T.b", "--force")[0] == OK
    research = [e for e in _umbrella_gate_events(repo) if e.data["gate"] == "research"]
    assert [e.data["reason"] for e in research] == ["mine"]


def test_a_gate_cannot_be_recorded_on_a_finished_umbrella(repo):
    """The guard's umbrella exemption is gone: a done umbrella is as closed as any item."""
    _split(repo)
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert run_cli(repo, "complete", "P.T.b", "--force")[0] == OK
    code, out, err = run_cli(
        repo, "gate", "record", "P.T", "research", "--outcome", "passed", "--evidence", "late"
    )
    assert code != OK, out + err


def test_a_refused_umbrella_completion_records_no_rolled_up_gates(repo):
    _split(repo)
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert run_cli(repo, "complete", "P.T")[0] != OK  # P.T.b still open
    assert not _umbrella_gate_events(repo)


def test_a_settled_umbrella_completed_directly_gets_rolled_up_gates(repo):
    _split(repo)
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    r = run_cli(repo, "complete", "P.T")
    assert r[0] == OK, r
    evs = _umbrella_gate_events(repo)
    assert evs and all(e.kind == "gate.skipped" for e in evs)
    assert {e.data["gate"] for e in evs} >= {"implement", "merge", "unit_tests"}


def test_a_failed_umbrella_gate_still_holds_the_umbrella(repo):
    _split(repo)
    log = EventLog(repo)
    for child in ("P.T.a", "P.T.b"):
        log.append("item.completed", child, {"sha": "", "kind": "task", "forced": True})
    out = run_cli(
        repo, "gate", "record", "P.T", "unit_tests", "--outcome", "failed", "--reason", "x"
    )
    assert out[0] == OK, out
    code, o, e = run_cli(repo, "complete", "P.T")
    assert code != OK and "failed on the umbrella itself" in o + e
