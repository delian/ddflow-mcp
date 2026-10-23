"""`bug found --item <the fix task of ANOTHER bug>` files the new bug's own task
(B8fb4a2ec4a).

`--item` says where a bug was found. An open fix task named there used to be taken as THE
fix of the new bug (so no task was filed and the bug was bound to a task fixing something
else). It is the fix only when it fixes no bug (a hand-filed bug-fix task) or fixes this
very one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog


def seed(repo: Path) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/p.py"]})
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    return log


def test_a_new_bug_found_on_another_bugs_fix_task_gets_its_own_task(repo):
    seed(repo)
    out = K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="By", agent="a")
    assert out.data["fix_task"] == "fix-By" and out.data["fix_task_filed"] is True, out.data
    st = state(repo)
    assert st.bugs["By"].fix_task == "fix-By" and st.items["fix-By"].fixes == ["By"]
    assert st.bugs["Bx"].fix_task == "fix-Bx"


def test_a_hand_filed_open_bug_fix_task_is_still_the_fix(repo):
    log = seed(repo)
    log.append("task.added", "B-fix-x", {"parent": "P1", "title": "fix x", "tags": ["bug"]})
    out = K.bug_found(repo, summary="by hand", item="B-fix-x", id="Bz", agent="a")
    assert out.data["fix_task"] == "B-fix-x" and out.data["fix_task_filed"] is False
    assert "fix-Bz" not in state(repo).items


def test_a_rereport_on_its_own_fix_task_still_names_it(repo):
    seed(repo)
    out = K.bug_found(repo, summary="again", item="fix-Bx", id="Bx", agent="a")
    assert out.data["fix_task"] == "fix-Bx" and out.data["fix_task_filed"] is False
