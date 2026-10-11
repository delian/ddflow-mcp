"""`wait --item --globs` is READY only when another agent's live lease does not hold them."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.core import outcome as O

HOLDER, WAITER = "agent-holder", "agent-waiter"


def _setup(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--globs", "f.py")
    run_cli(repo, "task", "add", "Y", "--globs", "other.py")
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok


def test_wait_on_unclaimed_item_with_held_globs_is_not_ready(repo):
    _setup(repo)
    out = A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER)
    assert out.data["woke"] is False, out.reason
    assert out.data["waiting_on"] == ["X"]


def test_wait_on_own_claimed_item_with_held_globs_is_not_ready(repo):
    """The lane already holds Y (narrow globs) and wants to widen to f.py, held by X."""
    _setup(repo)
    assert A.claim(repo, "Y", no_worktree=True, agent=WAITER).ok
    out = A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER)
    assert out.data["woke"] is False, out.reason


def test_wait_becomes_ready_after_the_holder_releases(repo):
    _setup(repo)
    assert A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER).data["woke"] is False
    run_cli(repo, "release", "X", "--agent", HOLDER)
    out = A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER)
    assert out.exit == O.OK and out.data["woke"] is True


def test_own_item_widening_wait_flips_to_ready_after_the_holder_releases(repo):
    _setup(repo)
    assert A.claim(repo, "Y", no_worktree=True, agent=WAITER).ok
    assert A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER).data["woke"] is False
    run_cli(repo, "release", "X", "--agent", HOLDER)
    out = A.wait(repo, item="Y", globs="f.py", timeout_s=0, agent=WAITER)
    assert out.exit == O.OK and out.data["woke"] is True
