"""B86e99de0c6: a done phase must hold no open task.

A phase that reports done must have zero open tasks. Filing a task under a DONE phase
(e.g. P-drivers done 18/18, then DRV-delta-from-descriptor filed into it) used to leave
the phase done while holding the open task — the live "done 18/19" inconsistency. The fix
re-opens a phase completed normally when it gains an open task, and refuses a new open
task under a truly-closed (imported) phase.
"""

from __future__ import annotations

from conftest import run_cli
from helpers import state as _state

from ddflow.core.model import DONE, OPEN
from ddflow.infra.log import EventLog


def _done_phase(repo, *, imported: bool = False) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P9", {"title": "P"})
    log.append("task.added", "P9.T1", {"parent": "P9", "title": "one"})
    log.append("item.completed", "P9.T1", {"kind": "task", "evidence": "done"})
    done: dict = {"kind": "phase", "evidence": "done"}
    if imported:
        done["imported"] = True
    log.append("item.completed", "P9", done)


def test_a_task_filed_under_a_done_phase_reopens_it(repo):
    _done_phase(repo)
    assert _state(repo).items["P9"].state == DONE

    code, _o, err = run_cli(repo, "task", "add", "P9.T2", "--title", "two", "--parent", "P9")
    assert code == 0, err

    st = _state(repo)
    assert st.items["P9"].state == OPEN, "a done phase must re-open when it gains an open task"
    assert st.items["P9.T2"].parent == "P9"


def test_a_closed_imported_phase_refuses_a_new_task(repo):
    _done_phase(repo, imported=True)
    assert _state(repo).items["P9"].state == DONE

    code, _o, err = run_cli(repo, "task", "add", "P9.T2", "--title", "two", "--parent", "P9")
    assert code == 3, (code, err)
    assert "P9.T2" not in _state(repo).items  # nothing filed
    assert _state(repo).items["P9"].state == DONE, "a truly-closed phase must refuse, not re-open"


def test_a_task_reopened_under_a_done_phase_reopens_it(repo):
    _done_phase(repo)
    assert _state(repo).items["P9"].state == DONE

    log = EventLog(repo, "seed")
    log.append("item.reopened", "P9.T1", {"reason": "did not hold"})

    st = _state(repo)
    assert st.items["P9.T1"].state == OPEN
    assert st.items["P9"].state == OPEN, "a phase reopens when a task under it becomes open again"


def test_a_task_reopened_under_a_closed_imported_phase_reopens_it(repo):
    _done_phase(repo, imported=True)
    assert _state(repo).items["P9"].state == DONE

    log = EventLog(repo, "seed")
    log.append("item.reopened", "P9.T1", {"reason": "did not hold"})

    st = _state(repo)
    assert st.items["P9.T1"].state == OPEN
    assert st.items["P9"].state == OPEN, "an imported phase reopens when its task is reopened"
