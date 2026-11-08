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
        args = ["task", "add", name, "--title", "task item"] + (["--globs", globs] if globs else [])
        run_cli(repo, *args)


def test_claim_refusal_lists_every_conflicting_holder(repo):
    # B-glob-refusal-names-one-conflict: one refusal must name every conflicting glob
    # WITH its holder, not just the first, so a caller does not re-claim once per held
    # glob to discover them all.
    _proj(repo, ("A", "src/a.py"), ("B", "src/b.py"), ("Y", "src/a.py,src/b.py"))
    assert A.claim(repo, "A", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "B", no_worktree=True, agent="agent-holder2").ok
    out = A.claim(repo, "Y", no_worktree=True, agent=WAITER)
    assert not out.ok
    # The refusal itself must pair each glob with its holder (the "Recorded:" waiter line
    # appended it after the first newline; the refusal clause is before it).
    main = out.reason.split("\n\nRecorded:")[0]
    assert "'src/a.py' overlaps 'src/a.py' held by agent-holder on A" in main
    assert "'src/b.py' overlaps 'src/b.py' held by agent-holder2 on B" in main


def test_claim_refusal_names_every_glob_one_holder_overlaps(repo):
    # One other holder overlapping on more than one glob must still name them all: a Clash
    # used to keep only the first pair, so its second glob was dropped from the refusal.
    _proj(repo, ("A", "src/a.py,src/b.py"), ("Y", "src/a.py,src/b.py"))
    assert A.claim(repo, "A", no_worktree=True, agent=HOLDER).ok
    out = A.claim(repo, "Y", no_worktree=True, agent=WAITER)
    assert not out.ok
    main = out.reason.split("\n\nRecorded:")[0]
    assert "'src/a.py' overlaps 'src/a.py' held by agent-holder on A" in main
    assert "'src/b.py' overlaps 'src/b.py' held by agent-holder on A" in main


def test_queued_behind_refusal_names_the_glob(repo):
    # `update --globs` used to say "queued behind sw-cedefaults" without naming a glob;
    # the queued-behind refusal must name the queued glob, not just who is ahead.
    _proj(repo, ("X", "a.py"), ("Z", "a.py"), ("Y", "a.py"))
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok
    assert not A.claim(repo, "Z", no_worktree=True, agent=QUEUER, queue=True).ok
    run_cli(repo, "release", "X", "--agent", HOLDER)
    out = A.claim(repo, "Y", no_worktree=True, agent=WAITER)
    assert not out.ok
    # The refusal clause itself (before the Recorded: trailer, which also names them) must
    # name the queued glob with its waiter, or finding them takes one claim per glob.
    main = out.reason.split("\n\nRecorded:")[0]
    assert "a.py queued behind" in main
    assert QUEUER in main


def test_queued_behind_refusal_names_every_queued_waiter(repo):
    # Two queued waiters on two different globs must both be named in ONE refusal, not
    # just the earliest (`queue_ahead` returned one waiter, B-glob-refusal-names-one-conflict).
    _proj(
        repo,
        ("X", "src/a.py,src/b.py"),
        ("Z", "src/a.py"),
        ("W", "src/b.py"),
        ("Y", "src/a.py,src/b.py"),
    )
    assert A.claim(repo, "X", no_worktree=True, agent=HOLDER).ok
    assert not A.claim(repo, "Z", no_worktree=True, agent=QUEUER, queue=True).ok
    assert not A.claim(repo, "W", no_worktree=True, agent="agent-queuer2", queue=True).ok
    run_cli(repo, "release", "X", "--agent", HOLDER)
    out = A.claim(repo, "Y", no_worktree=True, agent=WAITER)
    assert not out.ok
    main = out.reason.split("\n\nRecorded:")[0]
    assert "queued behind" in main
    assert "src/a.py" in main and QUEUER in main
    assert "src/b.py" in main and "agent-queuer2" in main
    # The structured waiting_on must name every ahead waiter too, not just the earliest:
    # wait reports it as who Y is waiting on, and a dropped waiter breaks that edge.
    waited = A.wait(repo, item="Y", timeout_s=0, agent=WAITER)
    assert waited.data["waiting_on"] == ["Z", "W"]


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
