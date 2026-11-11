"""[wait].max_call_seconds: one `ddflow wait` call returns still-waiting within it (D-TE-Q3).

A single `wait` (or `watch`) call never outlives `[wait].max_call_seconds` (default 240)
so a 5-minute subagent prompt cache stays warm and the waiters' capability to renew their
own leases ("heartbeat rides on it") is exercised by the re-call instead of one long call.
The cap applies ALWAYS -- a caller's explicit `--max-s` can still shorten a call but never
lengthen it past the cap.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.infra.log import EventLog

HOLDER, WAITER = "agent-holder", "agent-waiter"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--title", "T2 fixture", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    return repo


def test_max_call_seconds_defaults_to_240():
    assert Config().wait.max_call_seconds == 240


def test_a_wait_with_a_held_file_returns_within_max_call_seconds(proj, monkeypatch):
    """A wait asked for 600 s returns STILL-WAITING at the cap, not the ask: it slept
    the cap (no busy loop), and the answer is exit 2 naming the live holder."""
    monkeypatch.setenv("DDFLOW_WAIT_MAX_CALL_SECONDS", "1")
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=600, poll_s=0.05, agent=WAITER)
    elapsed = time.monotonic() - started
    assert 1 <= elapsed < 10, f"slept {elapsed:.1f}s: the 240-600 s ask, not zero (no spin)"
    assert out.exit == 2 and not out.data["woke"]
    assert out.data["holders"][0]["holder"] == HOLDER
    assert "Still blocked" in out.reason


def test_an_explicit_long_max_s_is_still_capped(proj, monkeypatch):
    """D-TE-Q3: the 240 s cap applies always -- `--max-s 600` cannot lengthen one call."""
    monkeypatch.setenv("DDFLOW_WAIT_MAX_CALL_SECONDS", "1")
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=600, max_s=600, poll_s=0.05, agent=WAITER)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"slept {elapsed:.1f}s: --max-s 600 must still stop at the cap"
    assert out.exit == 2 and not out.data["woke"]


def test_a_short_max_s_still_bounds_below_the_call_cap(proj, monkeypatch):
    """The caller's own bound SHORTENS a call below the cap (B-wait-lease-max-s-ignored)."""
    monkeypatch.setenv("DDFLOW_WAIT_MAX_CALL_SECONDS", "5")
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=600, max_s=0.3, poll_s=0.05, agent=WAITER)
    assert 0.3 <= time.monotonic() - started < 5
    assert out.exit == 2


def test_the_still_waiting_answer_carries_a_resume_token(proj, monkeypatch):
    """The exit-2 follow-up names the resume token, so an agent re-calls (its heartbeat
    riding on the next call) rather than a person re-stating the ask."""
    monkeypatch.setenv("DDFLOW_WAIT_MAX_CALL_SECONDS", "1")
    out = A.wait(proj, item="T2", timeout_s=60, poll_s=0.05, agent=WAITER)
    assert out.exit == 2
    token = out.data["wake"]["token"]
    assert token and token in out.reason


def test_a_blocked_wait_renews_the_waiters_own_lease_within_a_call(proj, monkeypatch):
    """Heartbeat rides on the short call: the waiter's OWN lease is renewed while it waits,
    so a wait cut to the cap has already kept its claim alive when it re-calls."""
    monkeypatch.setenv("DDFLOW_WAIT_MAX_CALL_SECONDS", "2")
    run_cli(proj, "task", "add", "T4", "--title", "T4 fixture", "--globs", "src/z.py")
    assert A.claim(proj, "T4", no_worktree=True, agent=WAITER).ok
    assert run_cli(proj, "config", "lease.heartbeat_s", "1", agent=WAITER)[0] == 0
    assert run_cli(proj, "config", "lease.renew_log_min_s", "0", agent=WAITER)[0] == 0
    before = _renewals(proj, "T4")
    out = A.wait(proj, item="T2", timeout_s=2, poll_s=0.2, agent=WAITER)
    assert not out.data["woke"]
    assert _renewals(proj, "T4") > before


def _renewals(repo: Path, item: str) -> int:
    return sum(
        1 for e in EventLog(repo, "x").read_all() if e.kind == "lease.renewed" and e.subject == item
    )
