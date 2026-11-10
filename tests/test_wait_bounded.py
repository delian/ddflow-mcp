"""`ddflow wait --max-s`: a bounded wait that names the live holder (DX-agent-wait-bounded)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.core import outcome as O

HOLDER, WAITER = "agent-holder", "agent-waiter"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--title", "T2 fixture", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    return repo


def test_max_s_bounds_a_long_timeout_and_names_the_live_holder(proj):
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=600, max_s=0.3, poll_s=0.05, agent=WAITER)
    elapsed = time.monotonic() - started
    assert 0.3 <= elapsed < 10  # it waited the bound, not the 600 s, not zero
    assert out.exit == 2 and not out.data["woke"]
    holder = out.data["holders"][0]
    assert holder["item"] == "T1" and holder["holder"] == HOLDER
    assert "held by agent-holder" in out.reason and "Try later" in out.reason
    assert holder["held_s"] >= 0 and holder["renewed_ago_s"] >= 0
    assert f"for {holder['held_s']}s (renewed {holder['renewed_ago_s']}s ago)" in out.reason


def test_without_max_s_the_timeout_alone_bounds_it(proj):
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=0.2, poll_s=0.05, agent=WAITER)
    assert time.monotonic() - started >= 0.2
    assert out.exit == 2 and out.data["holders"][0]["holder"] == HOLDER


def test_the_cli_flag_is_declared(proj):
    code, out, _err = run_cli(
        proj, "wait", "--item", "T2", "--max-s", "0.2", "--poll", "0.05", "--json", agent=WAITER
    )
    assert code == 2 and "agent-holder" in out


def test_a_negative_max_s_is_refused_not_read_as_zero(proj):
    out = A.wait(proj, item="T2", max_s=-1, agent=WAITER)
    assert out.exit != 0 and "max-s" in out.reason


def test_a_capped_wait_hands_back_the_token_its_advice_names(proj, monkeypatch):
    """Text mode says "resume with the token" and "run `ddflow wait` again" -- both must
    NAME the token, because `wake.token` is the only copy a non-json caller ever sees
    (B16e0083f21)."""
    monkeypatch.setenv("DDFLOW_LEASE_WAIT_CAP_S", "1")
    said: list[str] = []
    out = A.wait(proj, item="T2", timeout_s=5, poll_s=0.05, agent=WAITER, on_progress=said.append)
    assert out.exit == 2
    token = out.data["wake"]["token"]
    assert token, "a capped wait must carry a resume token"
    assert any(token in m for m in said), f"the cap note never names its token: {said}"
    assert token in out.reason, f"the exit-2 follow-up never names its token: {out.reason}"


def test_an_expired_lease_wait_is_refused_not_nothing_to_retry(repo):
    """`wait` on an item whose OWN lease expired under `reclaim_policy = report` can
    never clear on its own, so it answers "Not waiting" at once. That must be exit 3
    (refused), NOT exit 2 "nothing to do / try again": a caller that retries on 2, as
    the exit-2 message advises, spins -- 399 instant calls in 40 minutes here
    (Be68798098a)."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("ttl_s = 1800", "ttl_s = 1\ngrace_s = 0", 1))
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    time.sleep(1.5)
    out = A.wait(repo, item="T1", timeout_s=20, poll_s=0.05, agent=WAITER)
    assert out.exit == O.REFUSED, out.exit
    assert not out.data["waitable"]
    assert "Not waiting" in out.reason and "expired" in out.reason
