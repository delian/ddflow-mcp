"""Work tracking, and the loop detectors that stop work repeating forever.

The hard requirement on every detector here is NOT that it fires — it is that it stays
SILENT on healthy work. A loop detector that flags a normal project is one people
switch off, and then it is worth less than nothing, because everyone believes something
is watching. Each detector therefore gets a positive test, a negative test, and a
"just under the threshold" test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import UTC

from conftest import run_cli

from orchard.config import Config
from orchard.core import progress as PR
from orchard.core.model import fold
from orchard.infra.log import Event

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def ev(lamport, kind, subject, data=None, agent="a", ts=None):
    e = Event(
        kind=kind,
        subject=subject,
        data=data or {},
        agent=agent,
        lamport=lamport,
        ts=ts or f"2026-01-01T00:{lamport:02d}:00.000000Z",
    )
    return Event(**{**e.__dict__, "id": e.compute_id()})


def detect(events, cfg=None):
    cfg = cfg or Config()
    return PR.detect(events, fold(events, strict=False), cfg)


def kinds(findings):
    return sorted(f.kind for f in findings)


# -- work tracking --------------------------------------------------------------------


def test_attempts_are_counted_per_claim_not_per_item():
    evs = [ev(1, "task.added", "T", {"parent": "P"})]
    for i in range(3):
        evs.append(
            ev(
                2 + i * 2,
                "lease.acquired",
                "T",
                {"holder": "a", "at": 100.0 + i * 10, "ttl_s": 999},
            )
        )
        evs.append(ev(3 + i * 2, "lease.released", "T", {"holder": "a"}))
    w = PR.work(evs, fold(evs, strict=False))["T"]
    assert len(w.attempts) == 3


def test_a_completed_attempt_is_timed_to_its_COMPLETION_not_to_now():
    """Closing an attempt with `time.time()` reported elapsed-to-now for work finished
    days ago, silently inflating every duration in the report — numbers that looked
    precise and were wrong."""
    evs = [
        ev(1, "task.added", "T", {"parent": "P"}),
        ev(
            2,
            "lease.acquired",
            "T",
            {"holder": "a", "at": 1000.0, "ttl_s": 999},
            ts="2026-01-01T00:00:00.000000Z",
        ),
        ev(3, "item.completed", "T", {"sha": "abc"}, ts="2026-01-01T00:05:00.000000Z"),
    ]
    w = PR.work(evs, fold(evs, strict=False))["T"]
    assert w.attempts[0].ended_by == "completed"
    assert w.attempts[0].seconds == pytest.approx(300, abs=2), (
        f"expected the 5 minutes between the two event timestamps, got "
        f"{w.attempts[0].seconds}s — a completed attempt must be timed to its "
        f"completion, not to now, and on one clock rather than two"
    )


def test_an_open_attempt_reports_elapsed_to_now():
    """Still-running work is measured to NOW — that is what "how long has this been
    held" means, and it is the one case where `time.time()` is correct."""
    import time as _t
    from datetime import datetime, timedelta

    two_min_ago = (datetime.now(UTC) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    evs = [
        ev(1, "task.added", "T", {"parent": "P"}),
        ev(2, "lease.acquired", "T", {"holder": "a", "at": _t.time() - 120}, ts=two_min_ago),
    ]
    w = PR.work(evs, fold(evs, strict=False))["T"]
    assert w.attempts[0].open, "an unreleased attempt must read as open"
    assert 60 < w.attempts[0].seconds < 600, w.attempts[0].seconds


def test_gate_history_and_commits_are_tracked():
    evs = [
        ev(1, "task.added", "T", {"parent": "P"}),
        ev(2, "gate.failed", "T", {"gate": "unit_tests", "reason": "red"}),
        ev(3, "gate.passed", "T", {"gate": "unit_tests"}),
        ev(4, "worktree.merged", "T", {"sha": "deadbeef"}),
    ]
    w = PR.work(evs, fold(evs, strict=False))["T"]
    assert w.gate_outcomes["unit_tests"] == ["failed", "passed"]
    assert w.commits == ["deadbeef"]
    assert w.gate_runs == 2


# -- dependency cycles ------------------------------------------------------------------


def test_a_dependency_cycle_is_detected_and_always_blocking():
    evs = [
        ev(1, "phase.added", "P", {}),
        ev(2, "task.added", "A", {"parent": "P", "needs": ["C"]}),
        ev(3, "task.added", "B", {"parent": "P", "needs": ["A"]}),
        ev(4, "task.added", "C", {"parent": "P", "needs": ["B"]}),
    ]
    found = [f for f in detect(evs) if f.kind == "dependency_cycle"]
    assert found and found[0].severity == "block", "a cycle can never resolve itself"
    assert "->" in found[0].detail


def test_a_long_acyclic_chain_is_not_a_cycle():
    """The obvious false positive: depth is not circularity."""
    evs = [ev(1, "phase.added", "P", {})]
    for i in range(30):
        evs.append(
            ev(2 + i, "task.added", f"T{i}", {"parent": "P", "needs": [f"T{i - 1}"] if i else []})
        )
    assert "dependency_cycle" not in kinds(detect(evs))


# -- repeat claims -----------------------------------------------------------------------


def _thrash(n, ended="lease.released"):
    evs = [ev(1, "phase.added", "P", {}), ev(2, "task.added", "T", {"parent": "P"})]
    for i in range(n):
        evs.append(
            ev(3 + i * 2, "lease.acquired", "T", {"holder": "a", "at": 100.0 + i, "ttl_s": 999})
        )
        evs.append(ev(4 + i * 2, ended, "T", {"holder": "a"}))
    return evs


def test_repeat_claims_fires_at_the_threshold():
    cfg = Config()
    assert "repeat_claims" in kinds(detect(_thrash(cfg.loops.max_claims_per_item), cfg))


def test_repeat_claims_stays_quiet_just_under_the_threshold():
    cfg = Config()
    below = _thrash(cfg.loops.max_claims_per_item - 1)
    assert "repeat_claims" not in kinds(detect(below, cfg))


def test_crash_recovery_is_not_counted_as_thrashing():
    """An EXPIRED lease is a crashed agent — a different problem with a different
    remedy. Counting it here would blame the task for the machine dying."""
    evs = _thrash(6, ended="lease.expired")
    assert "repeat_claims" not in kinds(detect(evs))


def test_a_completed_item_is_never_reported_as_thrashing():
    evs = _thrash(6)
    evs.append(ev(99, "item.completed", "T", {"sha": "x"}))
    assert "repeat_claims" not in kinds(detect(evs))


# -- gate flapping ------------------------------------------------------------------------


def test_gate_flapping_is_detected():
    evs = [ev(1, "task.added", "T", {"parent": "P"})]
    for i in range(6):
        evs.append(
            ev(
                2 + i,
                "gate.passed" if i % 2 == 0 else "gate.failed",
                "T",
                {"gate": "unit_tests", "reason": "x"},
            )
        )
    found = [f for f in detect(evs) if f.kind == "gate_flapping"]
    assert found and "unit_tests" in found[0].detail


def test_a_gate_that_fails_then_passes_once_is_not_flapping():
    """The ordinary shape of fixing something. Must not fire."""
    evs = [
        ev(1, "task.added", "T", {"parent": "P"}),
        ev(2, "gate.failed", "T", {"gate": "unit_tests", "reason": "red"}),
        ev(3, "gate.passed", "T", {"gate": "unit_tests"}),
    ]
    assert "gate_flapping" not in kinds(detect(evs))


def test_unavailable_outcomes_do_not_count_as_flips():
    """An endpoint going up and down is not a gate changing its mind.

    The sequence ALTERNATES unavailable and passed on purpose: if the filter that keeps
    only decisive verdicts were dropped, this would read as eight verdict changes and
    fire. An all-`unavailable` sequence would pass either way and prove nothing —
    which is what the first version of this test did.
    """
    evs = [ev(1, "task.added", "T", {"parent": "P"})]
    for i in range(10):
        evs.append(
            ev(
                2 + i,
                "gate.unavailable" if i % 2 == 0 else "gate.passed",
                "T",
                {"gate": "critic", "reason": "endpoint down"},
            )
        )
    assert "gate_flapping" not in kinds(detect(evs)), (
        "an endpoint flapping up and down was mistaken for a verdict flapping"
    )


# -- reopened ------------------------------------------------------------------------------


def test_work_that_will_not_stay_done_is_detected():
    evs = [ev(1, "task.added", "T", {"parent": "P"})]
    for i in range(3):
        evs += [
            ev(2 + i * 2, "lease.acquired", "T", {"holder": "a", "at": 1.0 + i}),
            ev(3 + i * 2, "item.completed", "T", {"sha": f"s{i}"}),
        ]
    found = [f for f in detect(evs) if f.kind == "reopened"]
    assert found and found[0].count == 3


def test_completing_once_is_not_reopening():
    evs = [ev(1, "task.added", "T", {"parent": "P"}), ev(2, "item.completed", "T", {"sha": "x"})]
    assert "reopened" not in kinds(detect(evs))


# -- duplicate work -------------------------------------------------------------------------


def test_two_live_items_writing_the_same_files_are_reported():
    evs = [
        ev(1, "phase.added", "P", {}),
        ev(2, "task.added", "A", {"parent": "P", "globs": ["src/x.py"]}),
        ev(3, "task.added", "B", {"parent": "P", "globs": ["src/x.py"]}),
    ]
    found = [f for f in detect(evs) if f.kind == "duplicate_work"]
    assert found and "src/x.py" in found[0].detail


def test_a_finished_duplicate_is_not_reported():
    """Two items may legitimately touch one file over time — just not at once."""
    evs = [
        ev(1, "phase.added", "P", {}),
        ev(2, "task.added", "A", {"parent": "P", "globs": ["src/x.py"]}),
        ev(3, "task.added", "B", {"parent": "P", "globs": ["src/x.py"]}),
        ev(4, "item.completed", "A", {"sha": "x"}),
    ]
    assert "duplicate_work" not in kinds(detect(evs))


def test_items_with_no_declared_globs_are_not_duplicates_of_each_other():
    evs = [
        ev(1, "phase.added", "P", {}),
        ev(2, "task.added", "A", {"parent": "P"}),
        ev(3, "task.added", "B", {"parent": "P"}),
    ]
    assert "duplicate_work" not in kinds(detect(evs))


# -- no progress ------------------------------------------------------------------------------


def test_a_stalled_queue_is_detected():
    cfg = Config()
    cfg.loops.no_progress_window = 10
    evs = [ev(i, "session.note", "s", {"text": "thinking"}) for i in range(1, 15)]
    assert "no_progress" in kinds(detect(evs, cfg))


def test_a_queue_that_is_advancing_is_not_stalled():
    cfg = Config()
    cfg.loops.no_progress_window = 10
    evs = [ev(i, "session.note", "s", {"text": "x"}) for i in range(1, 12)]
    evs.append(ev(99, "gate.passed", "T", {"gate": "unit_tests"}))
    assert "no_progress" not in kinds(detect(evs, cfg))


def test_a_short_log_is_never_stalled():
    """A project three events old has not stalled; it has barely started."""
    cfg = Config()
    cfg.loops.no_progress_window = 50
    assert "no_progress" not in kinds(detect([ev(1, "session.note", "s", {})], cfg))


# -- the anti-false-positive guard, end to end ------------------------------------------------


def test_a_healthy_project_reports_no_loops_at_all(repo):
    """The property that matters most. A detector that flags normal work is switched
    off, and then it is worth less than nothing because everyone believes something is
    watching."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    for i in range(4):
        run_cli(
            repo,
            "task",
            "add",
            f"P1.T{i}",
            "--phase",
            "P1",
            "--globs",
            f"src/m{i}.py",
            *(["--needs", f"P1.T{i - 1}"] if i else []),
        )
    for i in range(4):
        run_cli(repo, "claim", f"P1.T{i}", "--no-worktree")
        for g in ("implement", "merge"):
            run_cli(repo, "gate", "record", f"P1.T{i}", g, "--outcome", "passed")
        run_cli(
            repo,
            "gate",
            "record",
            f"P1.T{i}",
            "unit_tests",
            "--outcome",
            "passed",
            "--evidence",
            "ok",
        )
        run_cli(
            repo,
            "gate",
            "record",
            f"P1.T{i}",
            "rubber_duck",
            "--outcome",
            "passed",
            "--evidence",
            "ok",
            "--model",
            "gemini-2.5-pro",
        )
        run_cli(repo, "complete", f"P1.T{i}", "--model", "claude-opus-5")

    code, out, _ = run_cli(repo, "loops")
    assert code == NOTHING, f"a healthy project was reported as looping:\n{out}"
    assert "No loops detected" in out


def test_block_mode_makes_claim_refuse_a_looping_item(repo):
    """A warning is read by a human later; a refused claim is read by the agent now."""
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "loops.on_detect", "block")
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "T", "--phase", "P1", "--globs", "x/*")
    for _ in range(4):
        run_cli(repo, "claim", "T", "--no-worktree")
        run_cli(repo, "release", "T", "--note", "gave up")

    code, _, err = run_cli(repo, "claim", "T", "--no-worktree")
    assert code == REFUSED, "an agent was allowed to keep spinning on a looping item"
    assert "already looping" in err and "repeat_claims" in err
    assert run_cli(repo, "claim", "T", "--no-worktree", "--force")[0] == OK


def test_loops_exits_two_when_there_is_nothing_wrong(repo):
    """Exit 2 is 'nothing to report', never confused with 0."""
    run_cli(repo, "init")
    assert run_cli(repo, "loops")[0] == NOTHING


def test_doctor_surfaces_loops(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "A", "--phase", "P1", "--needs", "B")
    run_cli(repo, "task", "add", "B", "--phase", "P1", "--needs", "A")
    code, out, _ = run_cli(repo, "doctor")
    assert code == FAIL
    assert "dependency_cycle" in out


def test_progress_reports_real_work(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "T", "--phase", "P1", "--globs", "x/*")
    run_cli(repo, "claim", "T", "--no-worktree")
    run_cli(repo, "gate", "record", "T", "unit_tests", "--outcome", "passed", "--evidence", "ok")
    rows = json.loads(run_cli(repo, "--json", "progress")[1])
    row = next(r for r in rows if r["item"] == "T")
    assert row["attempts"] == 1 and row["gate_runs"] == 1
    assert row["state"] == "running"
