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
    assert run_cli(repo, "release", "T1")[0] == 2  # nothing to release
    assert _state(repo) == "done"


def test_a_takeover_lease_released_on_a_crashed_item_keeps_it_running(repo):
    """A claim refused (or given up) on an item that was ALREADY running after a crash
    must not erase the crash: recover and brief keep flagging its worktree."""
    _claimed(repo)
    log = EventLog(repo)
    first = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.expired", "T1", {"holder": first.holder, "event": first.event})
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "second", "at": 2e9, "ttl_s": 1800, "globs": ["a.py"]},
    )
    taken = fold(log.read_all(), strict=False).items["T1"].lease
    assert taken.holder == "second" and taken.prior_state == "running"
    log.append("lease.released", "T1", {"holder": "second", "event": taken.event})
    assert _state(repo) == "running"


def test_a_holder_releasing_its_own_reclaimed_work_hands_it_back(repo):
    """B194: kilo-main's lease lapsed, it re-claimed its own item, then released it."""
    _claimed(repo)
    log = EventLog(repo)
    first = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.expired", "T1", {"holder": first.holder, "event": first.event})
    log.append(
        "lease.acquired",
        "T1",
        {"holder": first.holder, "at": 2e9, "ttl_s": 1800, "globs": ["a.py"]},
    )
    again = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.released", "T1", {"holder": again.holder, "event": again.event})
    assert _state(repo) == "open"


def test_a_takeover_that_started_work_and_is_released_hands_the_item_back(repo):
    _claimed(repo)
    log = EventLog(repo)
    first = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.expired", "T1", {"holder": first.holder, "event": first.event})
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "second", "at": 2e9, "ttl_s": 1800, "globs": ["a.py"]},
    )
    log.append("item.started", "T1", {})
    taken = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.released", "T1", {"holder": "second", "event": taken.event})
    assert _state(repo) == "open"


def test_a_rehomed_lease_released_later_hands_the_item_back(repo):
    """B190 re-homing: transfer release, then the new identity acquires (no item.started)."""
    _claimed(repo)
    log = EventLog(repo)
    old = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.released", "T1", {"holder": old.holder, "event": old.event, "transfer": True})
    log.append(
        "lease.acquired", "T1", {"holder": "me", "at": 2e9, "ttl_s": 1800, "globs": ["a.py"]}
    )
    assert _state(repo) == "running"
    now = fold(log.read_all(), strict=False).items["T1"].lease
    log.append("lease.released", "T1", {"holder": "me", "event": now.event})
    assert _state(repo) == "open"
