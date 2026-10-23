"""`claim <bug id>` claims the bug's fix task, filing it first when the bug has none
(Bee5f08be7b).

A bug filed without a fix task (`--no-task`, an upgrade, a hand-written log) left agents
creating and abandoning a throwaway task just to have something to claim.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog


def seed(repo: Path) -> None:
    run_cli(repo, "init")
    EventLog(repo, "seed").append("phase.added", "P1", {"title": "p"})


def test_claiming_a_bug_with_no_task_files_it_and_claims_it(repo):
    seed(repo)
    K.bug_found(repo, summary="stray", id="Bs", no_task=True, agent="a")
    assert "fix-Bs" not in state(repo).items
    out = LC.claim(repo, "Bs", agent="w", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["item"] == "fix-Bs" and out.data["bug"] == "Bs"
    st = state(repo)
    assert st.items["fix-Bs"].fixes == ["Bs"] and st.items["fix-Bs"].lease.holder == "w"
    assert st.bugs["Bs"].fix_task == "fix-Bs"


def test_claiming_a_bug_with_a_task_claims_that_task(repo):
    seed(repo)
    K.bug_found(repo, summary="has one", id="Bt", agent="a")
    out = LC.claim(repo, "Bt", agent="w", no_worktree=True)
    assert out.exit == 0 and out.data["item"] == "fix-Bt" and out.data["bug"] == "Bt"


def test_a_closed_or_unknown_id_is_still_no_such_item_and_files_nothing(repo):
    seed(repo)
    K.bug_found(repo, summary="x", id="Bc", no_task=True, agent="a")
    EventLog(repo, "x").append("bug.fixed", "Bc", {"regression_test": "t::x"})
    assert LC.claim(repo, "Bc", agent="w", no_worktree=True).exit != 0
    assert LC.claim(repo, "Bnope", agent="w", no_worktree=True).exit != 0
    assert set(state(repo).items) == {"P1"}


def test_the_cli_claims_a_bug_id(repo):
    seed(repo)
    K.bug_found(repo, summary="stray", id="Bs", no_task=True, agent="a")
    code, out, err = run_cli(repo, "claim", "Bs", "--no-worktree", agent="w")
    assert code == 0, out + err
    assert "fix-Bs" in out
    assert state(repo).items["fix-Bs"].lease.holder == "w"


def test_an_item_id_wins_over_a_bug_of_the_same_id(repo):
    seed(repo)
    EventLog(repo, "seed").append("task.added", "Bz", {"parent": "P1", "title": "t"})
    K.bug_found(repo, summary="z", id="Bz", no_task=True, agent="a")
    out = LC.claim(repo, "Bz", agent="w", no_worktree=True)
    assert out.data["item"] == "Bz" and "bug" not in out.data
