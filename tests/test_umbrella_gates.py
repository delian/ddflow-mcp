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
        e
        for e in EventLog(repo).read_all()
        if e.subject == "P.T" and e.kind.startswith("gate.")
    ]


def test_closing_the_last_child_gives_every_umbrella_gate_an_outcome(repo):
    _split(repo)
    assert run_cli(repo, "complete", "P.T.a", "--force")[0] == OK
    assert run_cli(repo, "complete", "P.T.b", "--force")[0] == OK
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["P.T"].state == "done"
    code, out, err = run_cli(repo, "gate", "status", "P.T")
    assert "never run" not in out + err
    evs = _umbrella_gate_events(repo)
    assert evs, "the umbrella completed with its gates silent"
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


def test_an_ordinary_item_with_silent_gates_is_still_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P", "--title", "phase")
    run_cli(repo, "task", "add", "P.T", "--title", "plain", "--globs", "a.py")
    code, out, err = run_cli(repo, "complete", "P.T")
    assert code != OK
    assert "never run" in out + err
    assert not _umbrella_gate_events(repo)
