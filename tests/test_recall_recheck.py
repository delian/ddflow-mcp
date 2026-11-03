"""A pre-recorded lesson a worker re-violates mid-task is re-surfaced on the next recheck
tick, not only at task start (Bcd5871e71b).

Lessons reach an agent at claim/brief time and on `ddflow recall`; a long task can violate
one afterward and never be told again. The heartbeat -- the one call an agent already makes
periodically during long gates -- now re-scores the live lessons against the item on a
cadence (`[cadence]`) and surfaces whatever still scores, bounded by `recall_recheck_s`
between runs and `recall_recheck_max` runs per item. The engine is `services.similar`, the
one recall already uses, not a second implementation.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import recall as RC

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

LESSON_ID = "L-rerecall-heartbeat"


def _queued(repo: Path, agent: str = "alpha") -> None:
    """A queue with a pre-recorded lesson and a claimed task built around the same words."""
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        LESSON_ID,
        "--title",
        "renew the lease with ddflow heartbeat",
        "--rule",
        "renew the lease on the claimed item with ddflow heartbeat during every long gate",
    )
    assert code == OK, out + err
    code, out, err = run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "renew the lease with heartbeat during the long gate",
        "--body",
        "renew the lease on the claimed item with ddflow heartbeat during the long gate",
        "--globs",
        "i/*",
        "--new",
    )
    assert code == OK, out + err  # --new: a task built around the lesson's own words is not a
    # duplicate of the lesson, but the add-time check rightly flags it -- answer "new"
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent=agent)
    assert code == OK, out + err  # --no-worktree: this test turns no files


def test_a_lesson_is_not_resurfaced_until_the_next_recheck_tick(repo):
    """At the claim instant (the tick `[cadence].recall_recheck_s` has not yet elapsed) nothing is
    due; one tick later it is -- the cadence, not the start, is what re-surfaces."""
    _queued(repo)
    st = fold(EventLog(repo).read_all(), strict=False)
    at = st.items["T1"].lease.acquired_at
    cfg = Config.load()
    assert RC.due(st, "T1", cfg, at) is False
    assert RC.due(st, "T1", cfg, at + cfg.cadence.recall_recheck_s) is True


def test_the_reviolated_lesson_is_what_the_tick_resurfaces(repo):
    """Only a live lesson scoring at least `[recall].recheck_floor` is listed -- the one the
    task is built around, not an unrelated one below the floor."""
    _queued(repo)
    code, out, err = run_cli(
        repo,
        "lesson",
        "add",
        "--id",
        "L-unrelated",
        "--title",
        "sorting the grocery list",
        "--rule",
        "buy eggs and milk before the store closes",
    )
    assert code == OK, out + err
    st = fold(EventLog(repo).read_all(), strict=False)
    hits = RC.matched_lessons(st, "T1", Config.load())
    ids = [rid for rid, _ in hits]
    assert LESSON_ID in ids
    assert all(st.lessons[rid].live for rid, _ in hits)
    assert "L-unrelated" not in ids


def test_a_recheck_does_not_busy_loop_two_heartbeats_in_the_interval(repo, monkeypatch):
    """Two heartbeats inside one `recall_recheck_s` interval surface (and record) once --
    the cadence record bounds it in the log, NOT the `recall_recheck_max` cap, which is set
    to 0 (no cap) here so the interval alone is what is tested."""
    _queued(repo, agent="beta")
    monkeypatch.setenv("DDFLOW_CADENCE_RECALL_RECHECK_S", "1")
    monkeypatch.setenv("DDFLOW_CADENCE_RECALL_RECHECK_MAX", "0")
    time.sleep(1.3)
    code, out, err = run_cli(repo, "heartbeat", "T1", agent="beta")
    assert code == OK, out + err
    assert LESSON_ID in out + err  # surfaced on this, the next tick
    code, out, err = run_cli(repo, "heartbeat", "T1", agent="beta")
    assert code == OK, out + err  # still a good heartbeat ...
    assert LESSON_ID not in out + err  # ... and nothing re-surfaced inside the interval
    st = fold(EventLog(repo).read_all(), strict=False)
    assert len(st.cadences.get(RC.cadence_id("T1"), [])) == 1


def test_recheck_opt_out_zero_disables_due(repo, monkeypatch):
    """`[cadence].recall_recheck_s = 0` opts out entirely; nothing is ever due, however late."""
    _queued(repo)
    monkeypatch.setenv("DDFLOW_CADENCE_RECALL_RECHECK_S", "0")
    st = fold(EventLog(repo).read_all(), strict=False)
    much_later = st.items["T1"].lease.acquired_at + 10_000_000
    assert RC.due(st, "T1", Config.load(), much_later) is False
