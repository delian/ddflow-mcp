"""An any-wait whose every blocker needs a person refuses at once (B02e99efc75).

`wait` with no item slept until its timeout when nothing was ready and some unrelated
agent held a lease -- even when everything blocked was in a dependency cycle or behind
an expired lease under `reclaim_policy = "report"`, which no release can clear. The
single-item wait already refused those at once; the any-wait did not ask.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.core import outcome as O

HOLDER, WAITER = "agent-holder", "agent-waiter"


def _busy_unrelated(repo: Path) -> None:
    """An agent holding something that blocks nobody, so `others` is not empty."""
    run_cli(repo, "task", "add", "BUSY", "--globs", "elsewhere.py")
    assert A.claim(repo, "BUSY", no_worktree=True, agent=HOLDER).ok


def test_an_any_wait_on_a_cycle_alone_does_not_sleep(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "C1", "--globs", "c1.py")
    run_cli(repo, "task", "add", "C2", "--needs", "C1", "--globs", "c2.py")
    run_cli(repo, "update", "C1", "--needs", "C2")
    _busy_unrelated(repo)
    started = time.monotonic()
    out = A.wait(repo, timeout_s=5, poll_s=0.05, agent=WAITER)
    assert time.monotonic() - started < 2, "slept to its timeout on a cycle"
    assert out.exit == O.NOTHING
    assert out.data["waitable"] is False
    assert "cycle" in out.reason


def test_an_any_wait_still_sleeps_when_something_is_waitable(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "C1", "--globs", "c1.py")
    run_cli(repo, "task", "add", "C2", "--needs", "C1", "--globs", "c2.py")
    run_cli(repo, "update", "C1", "--needs", "C2")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    out = A.wait(repo, timeout_s=0.3, poll_s=0.05, agent=WAITER)
    assert out.exit == O.NOTHING
    assert out.data["waitable"] is True, out.reason
    assert out.data["waiting_on"] == ["T1"]


def test_an_any_wait_on_an_expired_lease_alone_does_not_sleep(repo):
    run_cli(repo, "init")
    for k, v in (("lease.ttl_s", "1"), ("lease.grace_s", "0"), ("lease.reclaim_policy", "report")):
        code, _o, err = run_cli(repo, "config", "--set", k, v)
        assert code == 0, err
    run_cli(repo, "task", "add", "E1", "--globs", "e1.py")
    assert A.claim(repo, "E1", no_worktree=True, agent="agent-crashed").ok
    time.sleep(1.5)  # the crashed agent's lease lapses; `report` keeps it from anyone
    _busy_unrelated(repo)  # claimed after the lapse, so this one is live
    started = time.monotonic()
    out = A.wait(repo, timeout_s=5, poll_s=0.05, agent=WAITER)
    assert time.monotonic() - started < 2, "slept to its timeout on an expired lease"
    assert out.exit == O.NOTHING and out.data["waitable"] is False
    assert "expired" in out.reason


def test_a_dependency_on_work_in_motion_and_on_a_cycle_does_not_sleep(repo):
    """Every unmet dependency must be able to clear, not just one of them."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "C1", "--globs", "c1.py")
    run_cli(repo, "task", "add", "C2", "--needs", "C1", "--globs", "c2.py")
    run_cli(repo, "update", "C1", "--needs", "C2")
    run_cli(repo, "task", "add", "HELD", "--globs", "held.py")
    assert A.claim(repo, "HELD", no_worktree=True, agent=HOLDER).ok
    run_cli(repo, "task", "add", "D", "--needs", "HELD,C1", "--globs", "d.py")
    started = time.monotonic()
    out = A.wait(repo, timeout_s=5, poll_s=0.05, agent=WAITER)
    assert time.monotonic() - started < 2, "slept: C1 can never land, so D never clears"
    assert out.data["waitable"] is False, out.reason


def test_a_dependency_whose_own_blocker_clears_is_waited_for(repo):
    """D needs T2; T2 is blocked only by a live conflict -- D can clear, so wait."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "D", "--needs", "T2", "--globs", "d.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    out = A.wait(repo, timeout_s=0.3, poll_s=0.05, agent=WAITER)
    assert out.data["waitable"] is True, out.reason
