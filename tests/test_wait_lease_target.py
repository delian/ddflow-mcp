"""`wait --lease <holder|file>` waits for a LEASE, not for "anything ready" (Bff1731a132).

A bare `wait` returns the first ready item at once -- right for an idle agent, useless for one
whose files are held. `--lease` names what to wait on: an agent id (until it holds nothing) or
a path/glob (until no other agent's live lease covers it).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A

HOLDER, WAITER = "agent-holder", "agent-waiter"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T9", "--globs", "src/unrelated.py")  # ready, and unrelated
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    return repo


def test_a_bare_wait_returns_an_unrelated_ready_item(proj):
    out = A.wait(proj, timeout_s=0, agent=WAITER)
    assert out.data["ready"] == ["T9"], out.data  # the reported behaviour, documented


def test_wait_on_a_held_file_blocks_while_its_holder_holds_it(proj):
    out = A.wait(proj, lease="src/a.py", timeout_s=0, agent=WAITER)
    assert not out.data["woke"] and out.data["waiting_on"] == ["T1"], out.data
    assert "agent-holder" in out.reason, out.reason


def test_wait_on_a_holder_blocks_while_it_holds_anything(proj):
    out = A.wait(proj, lease=HOLDER, timeout_s=0, agent=WAITER)
    assert not out.data["woke"] and out.data["waiting_on"] == ["T1"], out.data


def test_wait_on_a_free_file_is_ready_at_once(proj):
    out = A.wait(proj, lease="src/free.py", timeout_s=0, agent=WAITER)
    assert out.data["woke"] and out.data["ready"] == ["src/free.py"], out.data


def test_wait_on_a_file_wakes_when_the_holder_releases(proj):
    def release():
        time.sleep(1.0)
        A.release(proj, "T1", agent=HOLDER)

    t = threading.Thread(target=release, daemon=True)
    t.start()
    out = A.wait(proj, lease="src/*.py", timeout_s=20, poll_s=0.2, agent=WAITER)
    t.join(5)
    assert out.data["woke"], out.data


def test_lease_and_item_together_are_refused(proj):
    out = A.wait(proj, item="T1", lease="src/a.py", timeout_s=0, agent=WAITER)
    assert out.exit == 1 and "lease" in out.reason, (out.exit, out.reason)
