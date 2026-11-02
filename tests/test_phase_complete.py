"""`ddflow phase complete <id>`: the deliberate, operator-gated retire of a settled phase.

A phase flips to done on its own only when every task under it completes. `phase close`
exists ONLY for work that predates ddflow (imported). This is the first-class door in
between: a person retires a phase whose every task is done or abandoned; an agent does the
same only by an operator approval, and the event recorded is `phase.completed`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state as _state

from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import DONE, OPEN
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _as_human(repo: Path, kind: str, subject: str, data: dict) -> None:
    EventLog(repo, "operator").append(kind, subject, data)


def _phase(repo: Path, *, settled: bool) -> Path:
    run_cli(repo, "init")
    _as_human(repo, "phase.added", "P9", {"title": "planned by hand"})
    _as_human(repo, "task.added", "P9.T1", {"parent": "P9", "title": "one"})
    _as_human(repo, "task.added", "P9.T2", {"parent": "P9", "title": "two"})
    if settled:
        _as_human(repo, "item.completed", "P9.T1", {"kind": "task", "evidence": "done"})
        _as_human(repo, "item.completed", "P9.T2", {"kind": "task", "evidence": "done"})
    return repo


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _approve(repo: Path, ask_id: str) -> None:
    digest = asks_of(_state(repo))[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def test_a_non_empty_phase_refuses_with_the_open_ids(repo):
    _phase(repo, settled=False)

    code, out, err = run_cli(repo, "phase", "complete", "P9")

    assert code == 3, (out, err)
    assert "still open" in out + err, out + err
    assert "P9.T1" in out + err and "P9.T2" in out + err, out + err
    assert _state(repo).items["P9"].state == OPEN, "a refusal wrote something"
    assert [e.kind for e in EventLog(repo).read_all() if e.kind == "phase.completed"] == []


def test_an_emptied_phase_completes_and_records_the_event(repo):
    _phase(repo, settled=True)
    st = _state(repo)
    assert all(t.terminal for t in st.tasks("P9")), "fixture: every task is finished"

    code, out, err = run_cli(repo, "phase", "complete", "P9")
    assert code == 0, (out, err)

    st = _state(repo)
    assert st.items["P9"].state == DONE, st.items["P9"].state
    assert "retired by operator" in st.items["P9"].completion_evidence
    evs = [e for e in EventLog(repo).read_all() if e.kind == "phase.completed"]
    assert [e.subject for e in evs] == ["P9"]
    assert evs[0].data["tasks"] == 2, evs[0].data
    assert evs[0].agent, "the event records who completed it"
    assert st.items["P9.T1"].state == DONE, "this retires the PHASE, it rewrites no task"


def test_an_agent_gets_an_approval_route(repo):
    _phase(repo, settled=True)

    code, _o, err = run_cli(repo, "phase", "complete", "P9", agent="bot")
    assert code == 3 and "--approval request" in err
    assert not asks_of(_state(repo))  # a routine refusal files nothing

    code, _o, err = run_cli(repo, "phase", "complete", "P9", "--approval", "request", agent="bot")
    assert code == 3
    ask_id = _ask_id(err)
    assert asks_of(_state(repo))[ask_id]["verb"] == "phase.complete"

    code, _o, err = run_cli(repo, "phase", "complete", "P9", "--approval", ask_id, agent="bot")
    assert code == 3 and "not answered" in err

    _approve(repo, ask_id)
    code, out, err = run_cli(repo, "phase", "complete", "P9", "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    ev = [e for e in EventLog(repo).read_all() if e.kind == "phase.completed"][-1]
    assert ev.data["approval"]["ask"] == ask_id and ev.data["approval"]["proxy"] == "bot"


def test_it_refuses_an_imported_phase_and_a_task_and_an_unknown_id(repo):
    """Distinct from `phase close`: imported work is that command's, and a task is not a phase."""
    run_cli(repo, "init")
    _as_human(repo, "phase.added", "P9", {"title": "t", "source": "docs/old.md"})
    _as_human(repo, "task.added", "P9.T1", {"parent": "P9", "title": "t"})
    _as_human(repo, "item.completed", "P9.T1", {"kind": "task", "evidence": "done"})

    code, out, err = run_cli(repo, "phase", "complete", "P9")
    assert code == 3 and "phase close" in out + err, (out, err)

    code, _o, _e = run_cli(repo, "phase", "complete", "P9.T1")
    assert code == 3 and "not a phase" in _o + _e

    code, _o, _e = run_cli(repo, "phase", "complete", "nope")
    assert code == 1, "an unknown id is a failure, not a refusal"
