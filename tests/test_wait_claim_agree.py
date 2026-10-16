"""`wait --item` and `claim` refuse the same things (B8843d3c7bd): READY is never followed by
a claim refused for files another agent holds or queued for."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A

HOLDER, WAITER, QUEUER = "agent-holder", "agent-waiter", "agent-queuer"


def _proj(repo, *tasks):
    run_cli(repo, "init")
    for name, globs in tasks:
        args = ["task", "add", name] + (["--globs", globs] if globs else [])
        run_cli(repo, *args)


def test_overlapping_lease_blocks_wait_names_holder_then_ready_and_claim_succeeds(repo):
    _proj(repo, ("X", "src/**"), ("Y", "src/a.py"))
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok
    out = A.wait(repo, item="Y", timeout_s=0, agent=WAITER)
    assert out.data["woke"] is False
    assert out.data["waiting_on"] == ["X"]
    assert HOLDER in out.reason
    assert not A.claim(repo, "Y", no_worktree=True, agent=WAITER).ok
    run_cli(repo, "release", "X", "--agent", HOLDER)
    out = A.wait(repo, item="Y", timeout_s=0, agent=WAITER)
    assert out.data["woke"] is True
    assert A.claim(repo, "Y", no_worktree=True, agent=WAITER).ok


def test_wait_is_not_ready_for_files_queued_behind_an_earlier_waiter(repo):
    _proj(repo, ("X", "a.py"), ("Y", "a.py"), ("Z", "a.py"))
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok
    assert not A.claim(repo, "Z", no_worktree=True, agent=QUEUER, queue=True).ok
    run_cli(repo, "release", "X", "--agent", HOLDER)
    claim = A.claim(repo, "Y", no_worktree=True, agent=WAITER)
    out = A.wait(repo, item="Y", timeout_s=0, agent=WAITER)
    assert not claim.ok and "queued behind" in claim.reason
    assert out.data["woke"] is False, out.reason
    assert out.data["waiting_on"] == ["Z"]


def test_wait_with_the_claim_globs_agrees_with_a_claim_that_names_them(repo):
    _proj(repo, ("X", "a.py"), ("Y", None))
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok
    out = A.wait(repo, item="Y", globs="a.py", timeout_s=0, agent=WAITER)
    assert out.data["woke"] is False
    assert not A.claim(repo, "Y", no_worktree=True, agent=WAITER, globs="a.py").ok
