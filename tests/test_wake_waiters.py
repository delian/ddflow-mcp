"""DX-wake-waiters: a waiter registers how to reach it (`wait --notify`) and the holder's
release/complete `woke` list surfaces that channel, so an orchestrator can resume it.

The wake itself (a waiter polling `ddflow wait` ends the moment the lease it waits on is
released) is already exercised elsewhere; this file pins the REACH channel: what the task
calls "a waiter registry maps waiter -> how to reach it".
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.services import waits as WT
from ddflow.services import watch as W

HOLDER, LANE = "holder", "lane"


@pytest.fixture
def proj(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "T2", "--title", "T2 fixture", "--globs", "src/a.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=HOLDER).ok
    return repo


def _registered_waiter(repo: Path) -> list[dict]:
    return WT.waiting_on(repo, "T1")


def _wait_until_registered(repo: Path, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(w["item"] == "T2" for w in _registered_waiter(repo)):
            return
        time.sleep(0.01)


def test_wait_notify_surfaces_the_reach_channel_in_the_release_woke_list(proj):
    t = threading.Thread(
        target=lambda: A.wait(
            proj,
            item="T2",
            agent=LANE,
            notify="send_message:session-1",
            timeout_s=5,
            poll_s=0.05,
        ),
        daemon=True,
    )
    t.start()
    _wait_until_registered(proj)
    rel = A.release(proj, "T1", agent=HOLDER)
    assert rel.ok
    woke = rel.data.get("woke", [])
    lane = [w for w in woke if w.get("agent") == LANE]
    assert lane, woke
    assert lane[0]["notify"] == "send_message:session-1"
    t.join(timeout=5)


def test_a_wait_token_carries_the_notify_channel(proj):
    out = A.wait(proj, item="T2", agent=LANE, notify="watch:http://inbound", timeout_s=0)
    token = out.data["wake"]["token"]
    assert W.decode_wait(token)["notify"] == "watch:http://inbound"
