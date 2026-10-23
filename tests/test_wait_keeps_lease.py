"""A blocked `wait` keeps the waiter's own leases alive (Bf81c097a89).

An agent blocked in `ddflow wait` for hours let its own lease lapse: nothing renewed it, so a
later `update --globs` reported success on a claim the commit hook no longer saw. A wait now
renews what its caller holds, like any other long command (`services.leasekeep`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.infra.log import EventLog

HOLDER, WAITER = "agent-holder", "agent-waiter"


def _renewals(repo: Path, item: str) -> int:
    return sum(
        1 for e in EventLog(repo, "x").read_all() if e.kind == "lease.renewed" and e.subject == item
    )


def test_a_blocked_wait_renews_the_waiters_own_lease(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T4", "--globs", "src/z.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    assert A.claim(repo, "T4", no_worktree=True, agent=WAITER).ok
    assert run_cli(repo, "config", "lease.heartbeat_s", "1", agent=WAITER)[0] == 0
    assert run_cli(repo, "config", "lease.renew_log_min_s", "0", agent=WAITER)[0] == 0
    before = _renewals(repo, "T4")
    out = A.wait(repo, item="T2", timeout_s=4, poll_s=0.2, agent=WAITER)
    assert not out.data["woke"], out.data  # still blocked by T1
    renewed = _renewals(repo, "T4") - before
    assert renewed > 0, "the waiter's lease on T4 was never renewed"
    assert renewed <= 5, f"renewed {renewed}x in 4s at heartbeat_s=1: once per poll, not per beat"
