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
    # Bx too: its own fix-Bx is the abandoned one (B974e34fa83).
    assert out.data["tasks"] == {"Brep": "fix-Brep", "Bx": "fix-Bx-2"}, out.data
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


def test_a_report_whose_own_fix_task_already_finished_is_linked_once(repo):
    """roborev job 1475 #2: a refile that meets an existing finished `fix-<bug>` links
    the bug to it once -- its own task now, so `verify --reopen` is the way back -- and
    is not relinked on every run."""
    log = seed(repo)
    log.append("task.added", "fix-Brep", {"parent": "P1", "title": "f", "fixes": ["Brep"]})
    log.append("item.completed", "fix-Brep", {"sha": "abc"})
    log.append("item.completed", "fix-Bx", {"sha": "abc"})
    out = K.bug_file_tasks(repo, agent="a")
    assert out.data["linked"] == ["Brep"] and out.data["filed"] == [], out.data
    assert state(repo).bugs["Brep"].fix_task == "fix-Brep"
    assert K.bug_file_tasks(repo, agent="a").exit == 2


def test_a_bug_its_abandoned_task_was_filed_to_fix_is_refiled(repo):
    """rubber_duck / roborev 1475 #1: an abandoned task is never sent back, so a bug in
    its `fixes` (a `--same-fix` report) needs a task of its own."""
    log = seed(repo)
    log.append("task.added", "T-both", {"parent": "P1", "title": "both", "fixes": ["Bx", "Brep"]})
    log.append("bug.found", "Brep", {"fix_task": "T-both"})
    log.append("item.abandoned", "T-both", {"reason": "superseded"})
    log.append("item.completed", "fix-Bx", {"sha": "abc"})  # not open to link to either
    out = K.bug_file_tasks(repo, agent="a")
    assert out.data["filed"] == ["Brep"], out.data
    assert state(repo).bugs["Brep"].fix_task == "fix-Brep"
    assert K.bug_file_tasks(repo, agent="a").exit == 2


def test_a_bugs_own_abandoned_fix_task_gets_a_successor(repo):
    """B974e34fa83: an abandoned `fix-<bug>` is never revived, so the bug is filed
    `fix-<bug>-2` (then `-3` when that is abandoned too), once each."""
    log = seed(repo)
    log.append("item.abandoned", "fix-Bx", {"reason": "looked invalid"})
    dry = K.bug_file_tasks(repo, dry_run=True, agent="a")
    assert "Bx" in dry.data["filed"], dry.data
    out = K.bug_file_tasks(repo, agent="a")
    assert out.data["tasks"]["Bx"] == "fix-Bx-2", out.data
    st = state(repo)
    assert st.bugs["Bx"].fix_task == "fix-Bx-2" and st.items["fix-Bx-2"].fixes == ["Bx"]
    assert st.items["fix-Bx"].state == "abandoned", "the abandoned one is left as it was"
    assert K.bug_file_tasks(repo, agent="a").exit == 2, "filed once"
    log.append("item.abandoned", "fix-Bx-2", {"reason": "again"})
    assert K.bug_file_tasks(repo, agent="a").data["tasks"]["Bx"] == "fix-Bx-3"


def test_the_successor_closes_the_bug_on_completion(repo):
    from conftest import pass_pipeline

    from ddflow.api import lifecycle as LC

    log = seed(repo)
    log.append("item.abandoned", "fix-Bx", {"reason": "looked invalid"})
    K.bug_file_tasks(repo, agent="a")
    t = repo / "tests" / "test_p_regress.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text("def test_last_line():\n    pass\n")
    pass_pipeline(repo, "fix-Bx-2")
    out = LC.complete(repo, "fix-Bx-2", model="claude-opus-5",
                      regression_test="tests/test_p_regress.py::test_last_line", agent="a")  # fmt: skip
    assert out.exit == 0 and out.data["bugs_closed"] == ["Bx"], out.data
