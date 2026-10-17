"""B8eb33fe297: a heartbeat that revives a lapsed lease catches it up however long it takes.

The revived lease keeps the TTL it was claimed with, so on a loaded machine (load average
300 in the ci gate) a heartbeat that spent longer than that TTL between renewing the lease
and catching it up to the item's resources found it lapsed again, skipped the catch-up
without a word and left `gpu:1` where the item said `gpu:3`. The catch-up is now judged at
the moment of the renewal, so how long the process takes to get from one to the other no
longer decides it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _wait import wait_until
from conftest import run_cli
from helpers import items_of as _items

from ddflow.api import items as AI
from ddflow.api import lifecycle as A
from ddflow.core import outcome as O
from ddflow.services import leases as L

HOLDER = "agent-holder"
TTL = 1  #: seconds the lease is claimed with: short, so that it lapses


def _lapsed_claim(repo: Path, monkeypatch) -> None:
    """T1 claimed with gpu:1 on a TTL-1 lease; the item then wants gpu:3, edited after the
    lease lapsed, so the edit reaches only the item."""
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "schedule.resources", '["gpu=4"]')[0] == O.OK
    assert run_cli(repo, "config", "--set", "lease.grace_s", "0")[0] == O.OK
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", str(TTL))
    got = A.claim(repo, "T1", no_worktree=True, resources="gpu:1", agent=HOLDER)
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    assert got.ok, got.reason
    lease = lambda: _items(repo)["T1"].lease  # noqa: E731
    wait_until(lambda: not lease().live(time.time(), 0), 10, what="the lease to lapse")
    assert AI.update(repo, "T1", AI.ItemEdit(resources=["gpu:3"]), agent=HOLDER).ok
    assert lease().resources == ["gpu:1"], "the edit of a lapsed lease reaches only the item"


def test_a_revived_lease_is_caught_up_even_when_the_heartbeat_is_slow(repo, monkeypatch):
    _lapsed_claim(repo, monkeypatch)
    real = L.renew

    def slow_after_renewing(*a, **k):
        renewed = real(*a, **k)
        time.sleep(TTL + 1.0)  # a loaded host: longer than the revived lease's whole TTL
        return renewed

    monkeypatch.setattr(L, "renew", slow_after_renewing)
    beat = A.heartbeat(repo, "T1", agent=HOLDER)
    assert beat.exit == O.OK, beat.reason
    got = _items(repo)["T1"].lease.resources
    assert got == ["gpu:3"], (got, beat.data)


def test_a_revived_lease_is_caught_up_when_nothing_is_slow(repo, monkeypatch):
    _lapsed_claim(repo, monkeypatch)
    beat = A.heartbeat(repo, "T1", agent=HOLDER)
    assert beat.exit == O.OK, beat.reason
    assert _items(repo)["T1"].lease.resources == ["gpu:3"], beat.data
