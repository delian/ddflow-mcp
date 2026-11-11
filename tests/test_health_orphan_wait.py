"""A lane waiting on a run that is already over is 'waiting on nothing' (DX-health-orphan-wait).

The live-run registry (`services.gates.running`) records every gate/review/ci/pins run; a
lane whose newest entry is finished, or whose process is gone with no recorded outcome, is
waiting on a DEAD run, not a long one. The watchdog must say so instead of nudging the
legitimate wait or missing the dead one."""

from __future__ import annotations

from ddflow.core import clock
from ddflow.core import health as H

A = "agent-a"
NOW = 1_000_000.0
LIM = H.Limits(stall_s=0, idle_s=0, orphan_s=300)


def _orphans(lane, run, now=NOW, orphan_s=300):
    lim = H.Limits(stall_s=0, idle_s=0, orphan_s=orphan_s)
    return H.inspect([lane], [], [], [], now, lim, [run])


def test_a_lane_waiting_on_a_finished_run_is_orphan_wait():
    lane = H.Lane("T1", A, True, NOW - 500)  # no progress for 500s
    run = H.Run("T1", "rubber_duck", outcome="passed", ended_at=NOW - 720)
    found = _orphans(lane, run)
    assert [(f.kind, f.item) for f in found] == [(H.ORPHAN_WAIT, "T1")]
    assert "ended 12m ago" in found[0].detail  # the END time, not the start time
    assert "rubber_duck" in found[0].detail and "passed" in found[0].detail
    assert "ddflow review T1 --gate rubber_duck" in found[0].fix


def test_a_finished_run_with_no_end_time_is_not_reported_as_just_ended():
    lane = H.Lane("T1", A, True, NOW - 500)
    run = H.Run("T1", "rubber_duck", outcome="passed")  # ended_at 0: when it ended is unknown
    found = _orphans(lane, run)
    assert [(f.kind, f.item) for f in found] == [(H.ORPHAN_WAIT, "T1")]
    assert "ended" not in found[0].detail  # not the false "ended now ago"
    assert "passed" in found[0].detail


def test_a_lane_waiting_on_a_dead_run_with_no_outcome_is_orphan_wait():
    lane = H.Lane("T1", A, True, NOW - 500)
    run = H.Run("T1", "ci", 77, NOW - 600, False, "/run/ci.log", "")
    found = _orphans(lane, run)
    assert [(f.kind, f.item) for f in found] == [(H.ORPHAN_WAIT, "T1")]
    assert "died with no outcome" in found[0].detail
    assert found[0].evidence["log_path"] == "/run/ci.log"


def test_a_lane_with_progress_since_the_run_is_not_orphan_wait():
    lane = H.Lane("T1", A, True, NOW - 30)  # progress since the run finished
    run = H.Run("T1", "rubber_duck", outcome="passed", started_at=NOW - 900)
    assert _orphans(lane, run) == []


def test_a_live_run_is_not_orphan_wait():
    lane = H.Lane("T1", A, True, NOW - 500)
    run = H.Run("T1", "rubber_duck", 4412, NOW - 90, True, "/run.log", "")
    assert _orphans(lane, run) == []


def test_orphan_s_zero_turns_the_check_off():
    lane = H.Lane("T1", A, True, NOW - 500)
    run = H.Run("T1", "ci", 77, NOW - 600, False, "/run/ci.log", "")
    assert _orphans(lane, run, orphan_s=0) == []


def test_the_run_marks_its_expected_duration_off_the_marker(tmp_path):
    from ddflow.services.gates import running as RUN

    m = RUN.mark_started(tmp_path, "T1", "ci", "tree-aaa", expected_s=1200)
    assert m is not None and m.expected_s == 1200
    (read,) = RUN.live_runs(tmp_path, "T1")  # this process's own pid: still alive
    assert read.expected_s == 1200


def test_a_live_run_past_its_expected_duration_is_not_live_work(tmp_path):
    from ddflow.services import healthwatch as HW
    from ddflow.services.gates import running as RUN

    m = RUN.mark_started(tmp_path, "T1", "ci", "tree-aaa", expected_s=300)
    assert m is not None
    now = clock.epoch(m.at) + 1000
    assert HW._live_run_items(tmp_path, {"T1": object()}, now) == set()


def test_a_live_run_within_its_expected_duration_is_live_work(tmp_path):
    from ddflow.services import healthwatch as HW
    from ddflow.services.gates import running as RUN

    m = RUN.mark_started(tmp_path, "T1", "ci", "tree-aaa", expected_s=300)
    assert m is not None
    now = clock.epoch(m.at) + 10
    assert HW._live_run_items(tmp_path, {"T1": object()}, now) == {"T1"}


def test_a_live_run_with_unknown_duration_is_live_work(tmp_path):
    from ddflow.services import healthwatch as HW
    from ddflow.services.gates import running as RUN

    m = RUN.mark_started(tmp_path, "T1", "ci", "tree-aaa")  # expected_s 0 = unknown
    assert m is not None
    now = clock.epoch(m.at) + 100000
    assert HW._live_run_items(tmp_path, {"T1": object()}, now) == {"T1"}
