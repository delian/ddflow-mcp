"""`ddflow wait --max-s`: a bounded wait that names the live holder (DX-agent-wait-bounded)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A

HOLDER, WAITER = "agent-holder", "agent-waiter"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    return repo


def test_max_s_bounds_a_long_timeout_and_names_the_live_holder(proj):
    started = time.monotonic()
    out = A.wait(proj, item="T2", timeout_s=600, max_s=0.3, poll_s=0.05, agent=WAITER)
    assert time.monotonic() - started < 10
    assert out.exit == 2 and not out.data["woke"]
    holder = out.data["holders"][0]
    assert holder["item"] == "T1" and holder["holder"] == HOLDER
    assert "held by agent-holder" in out.reason and "Try later" in out.reason


def test_without_max_s_the_timeout_alone_bounds_it(proj):
    out = A.wait(proj, item="T2", timeout_s=0.2, poll_s=0.05, agent=WAITER)
    assert out.exit == 2 and out.data["holders"][0]["holder"] == HOLDER


def test_the_cli_flag_is_declared(proj):
    code, out, _err = run_cli(
        proj, "wait", "--item", "T2", "--max-s", "0.2", "--poll", "0.05", "--json", agent=WAITER
    )
    assert code == 2 and "agent-holder" in out
