"""A deliberate release returns a RUNNING item to the queue; a crash (expiry) does not (B601fa7eff9)."""

from __future__ import annotations

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _state(repo, item="T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[item].state


def _claimed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    assert _state(repo) == "running"


def test_release_returns_a_running_item_to_open(repo):
    _claimed(repo)
    assert run_cli(repo, "release", "T1")[0] == 0
    assert _state(repo) == "open"
    _code, out, _ = run_cli(repo, "next")
    assert "INTERRUPTED" not in out and "T1" in out


def test_an_expired_lease_keeps_running_so_recovery_sees_it(repo):
    _claimed(repo)
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    EventLog(repo).append(
        "lease.expired", "T1", {"holder": item.lease.holder, "event": item.lease.event}
    )
    assert _state(repo) == "running"


def test_a_transfer_release_keeps_the_item_running(repo):
    _claimed(repo)
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    EventLog(repo).append(
        "lease.released",
        "T1",
        {"holder": item.lease.holder, "event": item.lease.event, "transfer": True},
    )
    assert _state(repo) == "running"


def test_release_after_completion_does_not_reopen(repo):
    _claimed(repo)
    run_cli(repo, "complete", "T1", "--force")
    assert _state(repo) == "done"
