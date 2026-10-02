"""Claims on a contended file are served first come, first served.

A claim refused for a glob overlap used to have no queue: whoever polled first after the
release took the file, and a hot one starved its longest waiter for hours. A live `wait`
(or a refused claim that is asked again) is now a place in line, and a younger or
unqueued claim of an overlapping file is refused as 'reserved for <waiter>' while that
waiter could claim. Dead, lapsed, self and disjoint waiters never block.
"""

from __future__ import annotations

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

HOLDER, B, C = "agent-a", "agent-b", "agent-c"


@pytest.fixture
def proj(repo: Path) -> Path:
    """A holds `hot` on src/mcp.py; B's TB and C's TC both write it; TD is disjoint."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "HOT", "--globs", "src/mcp.py")
    run_cli(repo, "task", "add", "TB", "--globs", "src/mcp.py")
    run_cli(repo, "task", "add", "TC", "--globs", "src/mcp.py")
    run_cli(repo, "task", "add", "TD", "--globs", "src/other.py")
    out = A.claim(repo, "HOT", no_worktree=True, agent=HOLDER)
    assert out.ok, out.reason
    return repo


def _queue(repo: Path, agent: str, item: str, since: float, **kw) -> None:
    """Register ``agent`` as a live waiter for ``item`` since ``since`` (this process's
    pid: alive for as long as the test runs)."""
    WT.register(
        repo,
        WT.Waiter(
            agent=agent,
            item=item,
            waiting_on=["HOT"],
            since=since,
            until=time.time() + 600,
            **kw,
        ),
    )


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _claim(repo: Path, item: str, agent: str) -> O.Outcome:
    return A.claim(repo, item, no_worktree=True, agent=agent)


def test_the_oldest_waiter_gets_the_freed_file_not_the_first_to_poll(proj):
    now = time.time()
    _queue(proj, B, "TB", now - 120)  # B registers waiting from t0
    _queue(proj, C, "TC", now - 60)  # C registers at t1
    assert A.release(proj, "HOT", agent=HOLDER).ok

    refused = _claim(proj, "TC", C)  # C polls first after the release
    assert refused.exit == O.REFUSED, refused.reason
    assert f"reserved for {B}" in refused.reason
    assert "waiting since" in refused.reason
    assert "waiter_reservation_s" in refused.reason

    assert _claim(proj, "TB", B).ok  # B takes it
    assert _claim(proj, "TC", C).exit == O.REFUSED  # now genuinely held
    assert A.release(proj, "TB", agent=B).ok
    assert _claim(proj, "TC", C).ok  # and C's turn comes


def test_an_unqueued_claimant_does_not_jump_the_line(proj):
    _queue(proj, B, "TB", time.time() - 30)
    assert A.release(proj, "HOT", agent=HOLDER).ok
    out = _claim(proj, "TC", "agent-newcomer")
    assert out.exit == O.REFUSED and f"reserved for {B}" in out.reason


def test_a_dead_waiter_never_blocks(proj):
    _queue(proj, B, "TB", time.time() - 120, pid=_dead_pid())
    assert A.release(proj, "HOT", agent=HOLDER).ok
    assert _claim(proj, "TC", C).ok


def test_a_waiter_past_its_deadline_never_blocks(proj):
    WT.register(
        proj,
        WT.Waiter(agent=B, item="TB", waiting_on=["HOT"], since=1.0, until=time.time() - 1),
    )
    assert A.release(proj, "HOT", agent=HOLDER).ok
    assert _claim(proj, "TC", C).ok


def test_the_reservation_lapses_after_the_window(proj):
    (proj / ".ddflow" / "config.toml").write_text("[lease]\nwaiter_reservation_s = 1\n")
    t = _wait_until_woken(proj, B, "TB")
    assert A.release(proj, "HOT", agent=HOLDER).ok
    t.join(20)
    assert _claim(proj, "TC", C).exit == O.REFUSED, "inside the window the place is kept"
    time.sleep(1.3)
    assert _claim(proj, "TC", C).ok, "the waiter did not come back: its place lapsed"


def test_the_waiters_own_claim_passes_and_spends_the_place(proj):
    _queue(proj, B, "TB", time.time() - 30)
    assert A.release(proj, "HOT", agent=HOLDER).ok
    assert _claim(proj, "TB", B).ok
    assert _claim(proj, "TB", B).ok, "re-claiming its own live lease is a renewal"
    assert [w for w in WT.live_waiters(proj) if w.agent == B] == []


def test_waiters_on_disjoint_files_never_block_each_other(proj):
    _queue(proj, B, "TB", time.time() - 120)  # needs src/mcp.py
    assert A.release(proj, "HOT", agent=HOLDER).ok
    assert _claim(proj, "TD", C).ok, "TD writes src/other.py: nothing is reserved on it"
    # And the reverse: a waiter for TD does not hold up TC's claim of mcp.py once B has it.
    _queue(proj, "agent-d", "TD", time.time() - 500)
    assert _claim(proj, "TB", B).ok


def test_a_waiter_still_behind_another_holder_reserves_nothing(proj):
    # B needs TB's files AND is blocked by another lease on them held by someone else:
    # the file freeing does not make B claimable, so C is not made to wait for B.
    run_cli(proj, "task", "add", "HOT2", "--globs", "src/mcp.py")
    _queue(proj, B, "TB", time.time() - 120)
    # HOT stays held by A: nothing is free, so C is simply blocked by the lease.
    out = _claim(proj, "TC", C)
    assert out.exit == O.REFUSED and "reserved" not in out.reason


def test_a_refused_claim_keeps_its_place_without_typing_wait(proj):
    first = _claim(proj, "TC", C)  # refused by A's lease: C is now in line
    assert first.exit == O.REFUSED
    assert [(w.agent, w.item) for w in WT.live_waiters(proj)] == [(C, "TC")]
    assert A.release(proj, "HOT", agent=HOLDER).ok
    out = _claim(proj, "TB", B)  # B never queued, and C was asking first
    assert out.exit == O.REFUSED and f"reserved for {C}" in out.reason
    assert _claim(proj, "TC", C).ok


def test_next_does_not_offer_what_is_reserved_for_someone_else(proj):
    _queue(proj, B, "TB", time.time() - 120)
    assert A.release(proj, "HOT", agent=HOLDER).ok
    out = A.next_(proj, agent=C)
    offered = [i["id"] for i in out.data["ready"]]
    assert "TC" not in offered and "TB" not in offered
    held = [b for b in out.data["blocked"] if "TC" in str(b)]
    assert held and "reserved for" in str(held)
    assert "TD" in offered


def test_wait_for_a_reserved_item_stays_blocked_and_names_the_waiter(proj):
    _queue(proj, B, "TB", time.time() - 120)
    assert A.release(proj, "HOT", agent=HOLDER).ok
    out = A.wait(proj, item="TC", timeout_s=0, agent=C)
    assert out.exit == O.NOTHING and f"reserved for {B}" in out.reason


def test_the_knob_zero_turns_the_queue_off(proj):
    (proj / ".ddflow" / "config.toml").write_text("[lease]\nwaiter_reservation_s = 0\n")
    _queue(proj, B, "TB", time.time() - 120)
    assert A.release(proj, "HOT", agent=HOLDER).ok
    assert _claim(proj, "TC", C).ok


def test_the_holder_is_told_in_its_brief_who_waits_on_it(proj):
    _queue(proj, B, "TB", time.time() - 120)
    text = A.brief(proj, agent=HOLDER).data["text"]
    assert "Waiting on you" in text and B in text and "TB" in text


def _wait_until_woken(repo: Path, agent: str, item: str) -> threading.Thread:
    """Start a real `wait` for ``item`` and return once it is registered."""
    t = threading.Thread(
        target=lambda: A.wait(repo, item=item, timeout_s=30, poll_s=0.05, agent=agent),
        daemon=True,
    )
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and not WT.live_waiters(repo):
        time.sleep(0.02)
    assert WT.live_waiters(repo), "the wait never registered"
    return t


def test_a_woken_wait_keeps_its_place_after_its_process_is_gone(proj):
    t = _wait_until_woken(proj, B, "TB")
    assert A.release(proj, "HOT", agent=HOLDER).ok
    t.join(20)
    kept = WT.live_waiters(proj)
    assert [(w.agent, w.item, w.woken) for w in kept] == [(B, "TB", True)]
    assert _claim(proj, "TC", C).exit == O.REFUSED
    assert _claim(proj, "TB", B).ok
    assert [w for w in WT.live_waiters(proj) if w.agent == B] == [], "claiming spends the place"
