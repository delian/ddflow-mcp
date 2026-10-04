"""`bug file-tasks` refiles a report left on a finished task it was not filed to fix
(B8dcbf2f8da).

`bug found --item <open fix task>` links the report to that task. Since B7bdcc6b212 the
task's completion leaves the report open, and `complete` now refiles it -- but a log
written before that, a completion by any other path, or an ABANDONED fix task still
leaves the bug open and pointing at a task that will never fix it. `bug file-tasks`
counted any item in the queue as a live fix task, so it never refiled them. A task's OWN
bug (its `fixes`) on a done task is left alone: `verify --reopen` is its way back.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as K
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def state(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def seed(repo: Path) -> EventLog:
    """Bug Bx with its fix task fix-Bx, and Brep reported against fix-Bx (linked to it)."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/p.py"]})
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Brep", agent="a")
    assert state(repo).bugs["Brep"].fix_task == "fix-Bx"
    return log


def test_a_report_left_on_a_done_fix_task_is_refiled(repo):
    log = seed(repo)
    # A completion that did not refile it (a log written before B7bdcc6b212's fix).
    log.append("bug.fixed", "Bx", {"regression_test": "t::x"})
    log.append("item.completed", "fix-Bx", {"sha": "abc"})
    dry = K.bug_file_tasks(repo, dry_run=True, agent="a")
    assert dry.data["filed"] == ["Brep"], dry.data
    out = K.bug_file_tasks(repo, agent="a")
    assert out.exit == 0 and out.data["filed"] == ["Brep"], out.data
    st = state(repo)
    assert st.bugs["Brep"].fix_task == "fix-Brep" and st.items["fix-Brep"].fixes == ["Brep"]
    assert K.bug_file_tasks(repo, agent="a").exit == 2, "refiled once, not on every run"


def test_a_report_left_on_an_abandoned_fix_task_is_refiled(repo):
    log = seed(repo)
    log.append("item.abandoned", "fix-Bx", {"reason": "superseded"})
    out = K.bug_file_tasks(repo, agent="a")
    assert out.data["filed"] == ["Brep"], out.data
    assert state(repo).bugs["Brep"].fix_task == "fix-Brep"


def test_a_tasks_own_bug_on_its_done_task_is_left_for_verify_reopen(repo):
    log = seed(repo)
    log.append("item.completed", "fix-Bx", {"sha": "abc"})  # Bx still open: did not hold
    out = K.bug_file_tasks(repo, agent="a")
    assert "Bx" not in out.data["filed"] + out.data["linked"], out.data
    assert state(repo).bugs["Bx"].fix_task == "fix-Bx"


def test_a_report_on_a_running_fix_task_is_still_its_fix(repo):
    seed(repo)
    out = K.bug_file_tasks(repo, agent="a")
    assert out.exit == 2, out.data
