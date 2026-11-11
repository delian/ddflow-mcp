"""LC-e2e: the release-at-enqueue + reuse MVP works end to end.

These are the SLOW scenarios: real processes, real worktrees. Each was first covered by
the fast tests in tests/test_release_at_enqueue.py; this file runs the composed flow — the
freed lane claims its next item while the integrator finishes the released one — which a
per-test split cannot observe. Marked `slow` and deselected by default, so run them by name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import append_config, run_cli
from test_merge_queue import _finish, _git, _landed, _status, proj  # noqa: F401
from test_release_at_enqueue import _item, _lease_of, _release  # noqa: F401

from ddflow.services import inbox as IB
from ddflow.services import mergequeue as MQ


@pytest.fixture
def rp(proj: Path) -> Path:
    err, _ = append_config(proj, '[agent]\nfamily = "anthropic"\n')
    assert not err
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "author family")
    return proj


@pytest.mark.slow
def test_a_freed_lane_claims_its_next_item_while_the_integrator_merges_the_released_one(rp):
    """Scenario 1: T1 is queued with --release, its lane claims T2 WHILE the integrator
    runs ci/unit_tests (blocked on the suite lock) and merges T1."""
    _finish(rp, "T1")
    code, out, err = run_cli(
        rp, "task", "add", "T2", "--title", "knob t2", "--globs", "knobs/t2.txt"
    )
    assert code == 0, out + err  # a second, open item the freed lane can take next
    assert _release(rp, "T1")[0] == 0
    hold = rp.parent / "hold"
    hold.write_text("")
    integrator = subprocess.Popen(
        [
            sys.executable, "-m", "ddflow", "--repo", str(rp), "--agent", "one", "queue", "run"
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(1200):
            if MQ.holder(rp):
                break
            time.sleep(0.05)
        assert MQ.holder(rp), "the integrator never took the lock"
        # while the integrator holds the suite lock, the freed lane takes the next item
        code, out, err = run_cli(rp, "--json", "next", agent="t1")
        assert code == 0, out + err
        ready = [i.get("id") for i in json.loads(out).get("ready", [])]
        assert "T2" in ready, ready
        code, out, err = run_cli(rp, "--json", "claim", "T2", agent="t1")
        assert code == 0, out + err
    finally:
        hold.unlink(missing_ok=True)
        integrator.communicate(timeout=120)
    assert integrator.returncode == 0, integrator.stderr
    assert _landed(rp) == ["T1"]
    assert _item(rp, "T1")["state"] == "done", "the item did not complete without its agent"
    assert _lease_of(rp, "T2"), "the freed lane did not take the next item"


@pytest.mark.slow
def test_a_red_integrator_run_wakes_the_lane_and_the_item_is_not_lost(rp):
    """Scenario 2: a red integrator run parks the item, wakes the lane with the resume
    brief, and the item is not finished (and not lost)."""
    _finish(rp, "T1", "BAD\n")
    assert _release(rp, "T1")[0] == 0
    code, out, err = run_cli(rp, "queue", "run", agent="integrator")
    assert code == 1, out + err
    assert _status(rp)["T1"]["state"] == "parked"
    assert _item(rp, "T1")["state"] != "done"
    texts = [m.text for m in IB.pending(rp, "t1") if m.reason == "message"]
    assert texts and "PARKED" in texts[0] and "ddflow claim T1" in texts[0], texts


@pytest.mark.slow
def test_release_is_refused_while_a_judgement_gate_is_open(rp):
    """Scenario 3: an item whose judgement gate is not recorded cannot be released."""
    _finish(rp, "T1")
    r = run_cli(
        rp,
        "gate",
        "record",
        "T1",
        "critic",
        "--outcome",
        "unavailable",
        "--reason",
        "no reviewer",
        "--evidence",
        "x",
    )
    assert r[0] == 0, r
    code, out, err = _release(rp, "T1")
    assert code == 3 and "critic" in out + err, out + err
    assert MQ.get(rp, "T1") is None
    assert _lease_of(rp, "T1"), "a refused release must leave the lease alone"


@pytest.mark.slow
def test_no_lease_is_held_while_the_integrator_waits_for_the_suite_lock(rp):
    """Scenario 4: the lease is gone while the integrator waits for the suite lock (the
    suite blocks on a hold file), so the agent is not hold-and-waiting a file lease."""
    _finish(rp, "T1")
    assert _release(rp, "T1")[0] == 0
    hold = rp.parent / "hold"
    hold.write_text("")
    first = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(rp), "--agent", "one", "queue", "run"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(1200):
            if MQ.holder(rp):
                break
            time.sleep(0.05)
        assert MQ.holder(rp), "the integrator never took the lock"
        assert not _lease_of(rp, "T1"), "hold-and-wait: a lease is held during the suite"
    finally:
        hold.unlink(missing_ok=True)
        first.communicate(timeout=120)
    assert first.returncode == 0 and _item(rp, "T1")["state"] == "done"
