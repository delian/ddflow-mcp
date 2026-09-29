"""`wait`: an agent blocked on another agent's claim is woken when the claim ends.

Before this, a refused claim left the waiter two moves -- poll `next` on a timer it had to
invent, or stop and wait for a person to say "try again" -- and the holder was never told
anyone was waiting. These tests run two identities against one real log: one holds, one
waits in the foreground, and a thread plays the holder finishing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.core import outcome as O
from ddflow.services import waits as WT

HOLDER, WAITER = "agent-holder", "agent-waiter"


@pytest.fixture
def proj(repo: Path) -> Path:
    """T1 held by HOLDER; T2 writes the same file; T3 depends on T1."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T3", "--needs", "T1", "--globs", "src/other.py")
    out = A.claim(repo, "T1", no_worktree=True, agent=HOLDER)
    assert out.ok, out.reason
    return repo


def _later(delay: float, fn) -> tuple[threading.Thread, dict]:
    """Run ``fn`` on a thread after ``delay``; its return value lands in ``box``."""
    box: dict = {}

    def run() -> None:
        time.sleep(delay)
        box["out"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


def test_a_conflicted_waiter_wakes_when_the_holder_releases(proj):
    t, box = _later(0.4, lambda: A.release(proj, "T1", agent=HOLDER))
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=20, poll_s=0.05, agent=WAITER)
    t.join(5)
    assert out.exit == O.OK, out.reason
    assert out.data["woke"] and out.data["ready"] == ["T2"]
    assert out.data["freed_by"] == ["T1: lease released"]
    assert time.monotonic() - started < 10, "woke by timeout, not by the release"
    # The release is what woke it, and the release SAYS so to the holder.
    assert [w["agent"] for w in box["out"].data["woke"]] == [WAITER]
    assert WT.live_waiters(proj) == [], "a finished wait left its registration behind"


def test_the_holder_is_told_at_heartbeat_who_it_is_holding_up(proj):
    seen: dict = {}

    def beat_then_release():
        seen["hb"] = A.heartbeat(proj, "T1", agent=HOLDER)
        return A.release(proj, "T1", agent=HOLDER)

    t, _ = _later(0.4, beat_then_release)
    out = A.wait(proj, item="T2", timeout_s=20, poll_s=0.05, agent=WAITER)
    t.join(5)
    assert out.ok
    rows = seen["hb"].data["waiters"]
    assert [(w["agent"], w["item"], w["waiting_on"]) for w in rows] == [(WAITER, "T2", ["T1"])]


def test_a_dependency_waiter_wakes_when_the_dependency_completes(proj):
    t, box = _later(0.4, lambda: A.complete(proj, "T1", force=True, agent=HOLDER))
    out = A.wait(proj, item="T3", timeout_s=20, poll_s=0.05, agent=WAITER)
    t.join(5)
    assert out.exit == O.OK, out.reason
    assert out.data["freed_by"] == ["T1: done"]
    assert [w["agent"] for w in box["out"].data["woke"]] == [WAITER]


def test_waiting_for_anything_wakes_when_anything_frees(proj):
    # T2 conflicts and T3 needs T1: with T1 held, nothing is ready for the waiter.
    t, _ = _later(0.4, lambda: A.release(proj, "T1", agent=HOLDER))
    out = A.wait(proj, timeout_s=20, poll_s=0.05, agent=WAITER)
    t.join(5)
    assert out.exit == O.OK, out.reason
    assert "T1" in out.data["ready"] or "T2" in out.data["ready"]


def test_a_deadline_is_exit_2_and_says_what_it_still_waits_on(proj):
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=0.3, poll_s=0.05, agent=WAITER)
    assert out.exit == O.NOTHING
    assert out.data["waitable"] and not out.data["woke"]
    assert out.data["waiting_on"] == ["T1"]
    assert "Still blocked" in out.reason
    assert time.monotonic() - started < 5
    assert WT.live_waiters(proj) == []


def test_timeout_zero_asks_without_sleeping(proj):
    out = A.wait(proj, item="T2", timeout_s=0, agent=WAITER)
    assert out.exit == O.NOTHING and out.data["waitable"]
    assert WT.live_waiters(proj) == [], "a non-blocking check must not register a wait"


def test_an_item_that_is_already_free_returns_at_once(proj):
    out = A.wait(proj, item="T1", timeout_s=20, agent=HOLDER)
    assert out.ok and out.data["ready"] == ["T1"], "the holder's own item is not a wait"
    A.release(proj, "T1", agent=HOLDER)
    out = A.wait(proj, item="T2", timeout_s=20, agent=WAITER)
    assert out.ok and out.data["waited_s"] == 0


@pytest.mark.parametrize(
    "setup, item, needle",
    [
        # A cycle does not clear when anyone finishes.
        (
            [("task", "add", "C1", "--needs", "C2"), ("task", "add", "C2", "--needs", "C1")],
            "C1",
            "cycle",
        ),
        # Nobody is working on the dependency: take it instead of waiting.
        ([("task", "add", "D1"), ("task", "add", "D2", "--needs", "D1")], "D2", "nobody"),
        # An operator's hold needs the operator.
        ([("task", "add", "H1"), ("block", "H1", "--reason", "parked")], "H1", "act on it"),
    ],
)
def test_a_wait_nothing_can_end_is_refused_up_front(proj, setup, item, needle):
    for argv in setup:
        run_cli(proj, *argv)
    started = time.monotonic()
    out = A.wait(proj, item=item, timeout_s=30, poll_s=0.05, agent=WAITER)
    assert out.exit == O.NOTHING and not out.data["waitable"], out.reason
    assert needle in out.reason
    assert time.monotonic() - started < 5, "it slept on a wait that could never end"


def test_waiting_on_a_dependency_you_hold_yourself_is_refused(proj):
    out = A.wait(proj, item="T3", timeout_s=30, agent=HOLDER)
    assert out.exit == O.NOTHING and not out.data["waitable"]
    assert "YOU hold" in out.reason


def test_a_done_item_is_not_waited_on(proj):
    A.complete(proj, "T1", force=True, agent=HOLDER)
    out = A.wait(proj, item="T1", timeout_s=30, agent=WAITER)
    assert out.exit == O.NOTHING and not out.data["waitable"]
    assert "already done" in out.reason


def test_an_unknown_item_fails(proj):
    out = A.wait(proj, item="NOPE", timeout_s=30, agent=WAITER)
    assert out.exit == O.FAIL
    assert set(out.data) >= {"woke", "waitable", "ready", "waiting_on"}, "shape must be stable"


def test_waiting_for_anything_with_nothing_in_flight_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "C1", "--needs", "C2")
    run_cli(repo, "task", "add", "C2", "--needs", "C1")
    out = A.wait(repo, timeout_s=30, agent=WAITER)
    assert out.exit == O.NOTHING and not out.data["waitable"]
    assert "no other agent holds anything" in out.reason


def test_an_expired_lease_wakes_the_waiter_although_no_event_is_written(repo, monkeypatch):
    """Expiry is time passing, not an append -- so extent() never changes. The periodic
    re-check is the only thing that notices, and without it this waits to its deadline."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    assert "ttl_s = 1800" in cfg.read_text()
    # The TTL is fixed when the lease is taken, so it is shortened BEFORE the claim.
    cfg.write_text(cfg.read_text().replace("ttl_s = 1800", "ttl_s = 1\ngrace_s = 0", 1))
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    monkeypatch.setattr(WT, "RECHECK_S", 0.2)
    out = A.wait(repo, item="T2", timeout_s=20, poll_s=0.05, agent=WAITER)
    assert out.exit == O.OK, out.reason
    assert out.data["freed_by"] == ["T1: lease expired"]


def test_the_claim_refusal_points_at_wait(proj):
    out = A.claim(proj, "T2", no_worktree=True, agent=WAITER)
    assert out.exit == O.REFUSED
    assert "ddflow wait --item T2" in out.reason


def test_next_points_at_wait_when_a_release_would_help(proj):
    out = A.next_(proj, agent=WAITER)
    # T1 is held; everything else is blocked behind it.
    assert out.exit == O.NOTHING
    assert "ddflow wait" in out.reason


# -- the registry ----------------------------------------------------------------------


def test_a_dead_waiter_is_pruned_and_never_reported(repo):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    WT.register(repo, WT.Waiter(agent="ghost", item="X", waiting_on=["Y"], pid=p.pid))
    live = WT.register(
        repo, WT.Waiter(agent="me", item="X", waiting_on=["Y"], until=time.time() + 60)
    )
    assert [w.agent for w in WT.live_waiters(repo)] == ["me"]
    assert len(list((repo / WT.WAITS_DIR).glob("*.json"))) == 1, "the dead one was not pruned"
    assert [w["agent"] for w in WT.waiting_on(repo, "Y")] == ["me"]
    assert WT.waiting_on(repo, "Z") == []
    WT.unregister(live)
    assert WT.live_waiters(repo) == []


def test_a_waiter_past_its_deadline_is_not_live(repo):
    WT.register(repo, WT.Waiter(agent="late", item="X", until=time.time() - 1, pid=os.getpid()))
    assert WT.live_waiters(repo) == []


def test_the_registry_is_never_committed(repo):
    run_cli(repo, "init")
    WT.register(repo, WT.Waiter(agent="me", item="X", until=time.time() + 60))
    st = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "waits" not in st.stdout, st.stdout


def test_an_expired_lease_on_the_item_ITSELF_is_not_a_wake(repo, monkeypatch):
    """Expiry frees the holder's GLOBS, but not its item: `claim` refuses an expired
    lease under `reclaim_policy = report` (a crashed agent's tree may hold finished
    work). Reporting it claimable made wait -> claim refused -> wait -> "ready" a spin."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("ttl_s = 1800", "ttl_s = 1\ngrace_s = 0", 1))
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    monkeypatch.setattr(WT, "RECHECK_S", 0.2)
    out = A.wait(repo, item="T1", timeout_s=20, poll_s=0.05, agent=WAITER)
    assert out.exit == O.NOTHING and not out.data["waitable"], out.reason
    assert "ddflow recover" in out.reason
    refused = A.claim(repo, "T1", no_worktree=True, agent=WAITER)
    assert refused.exit == O.REFUSED, "if claim accepts it, wait should have said ready"


def test_the_holder_hears_who_woke_even_when_the_waiter_is_quick(proj, monkeypatch):
    """Waiters are read BEFORE the lease is let go. Read after, a waiter that wakes and
    unregisters inside that window was never reported to the holder at all."""
    WT.register(proj, WT.Waiter(agent=WAITER, item="T2", waiting_on=["T1"], until=time.time() + 60))
    real = A.L.release

    def release_then_waiter_leaves(*a, **k):
        ok = real(*a, **k)
        for w in WT.live_waiters(proj):
            WT.unregister(w)
        return ok

    monkeypatch.setattr(A.L, "release", release_then_waiter_leaves)
    out = A.release(proj, "T1", agent=HOLDER)
    assert [w["agent"] for w in out.data["woke"]] == [WAITER]
