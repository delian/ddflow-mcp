"""Bc0b26248e3: a bug in ddflow itself (--scope ddflow), found while working on an item, does
not give its fix task that item's files (its brief then listed the globs of another fix)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog
from ddflow.services.bug_globs import inherit_globs

SRC = ["ddflow/api/lifecycle/claim.py", "tests/test_claim.py"]


def _seed(repo):
    run_cli(repo, "init")
    for f in ["ddflow/api/lifecycle/claim.py", "tests/test_claim.py"]:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": SRC})
    log.append("item.completed", "T1", {"sha": "abc"})


def test_a_tool_bug_naming_no_file_inherits_nothing():
    assert inherit_globs(SRC, "the parser drops a line", tool_bug=True) == []
    assert inherit_globs(SRC, "the parser drops a line") == SRC  # a project bug still does


def test_a_tool_bug_quoting_a_source_file_inherits_nothing_either():
    """The files a report quotes are as often the symptom as the cause (Bc0b26248e3 itself)."""
    assert inherit_globs(SRC, "the brief lists claim.py of another fix", tool_bug=True) == []
    assert inherit_globs(SRC, "the brief lists claim.py of another fix") == [SRC[0]]


def test_bug_found_scope_ddflow_files_a_fix_task_without_the_items_globs(repo):
    _seed(repo)
    K.bug_found(repo, summary="no bug show command", item="T1", id="Bd", scope="ddflow", agent="a")
    assert state(repo).items["fix-Bd"].globs == []
    K.bug_found(repo, summary="the parser drops a line", item="T1", id="Bp", agent="a")
    assert state(repo).items["fix-Bp"].globs == SRC
    # a report that quotes the item's own files (as Bc0b26248e3 does) inherits nothing either
    K.bug_found(
        repo, summary="the brief of fix-X lists claim.py and tests/test_claim.py", item="T1",
        id="Bq", scope="ddflow", agent="a",
    )  # fmt: skip
    assert state(repo).items["fix-Bq"].globs == []


def test_file_tasks_keeps_the_scope_when_it_files_the_task_later(repo):
    _seed(repo)
    K.bug_found(
        repo, summary="no bug show command", item="T1", id="Bd", scope="ddflow", no_task=True,
        agent="a",
    )  # fmt: skip
    K.bug_found(
        repo, summary="the parser drops a line", item="T1", id="Bp", no_task=True, agent="a"
    )  # fmt: skip
    assert "fix-Bd" not in state(repo).items
    K.bug_file_tasks(repo, agent="a")
    assert state(repo).items["fix-Bp"].globs == SRC  # the deferred path does have the item
    assert state(repo).items["fix-Bd"].globs == []
