"""Log-derived flow signals: pure functions of the events, the fold and an injected clock.

Everything here is hand-built Events and a fake ``now``: no repository, no companions, no
network, no host signal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ddflow.config import Config
from ddflow.core import flowsignals as FS
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.core.schedule import plan

NOW = 1_800_000_000.0
MIN = 60.0
DAY = 86400.0


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Log:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, kind: str, subject: str, at: float = NOW, **data) -> None:
        self.events.append(
            Event(kind=kind, subject=subject, data=data, lamport=len(self.events) + 1, ts=_ts(at))
        )

    def review(
        self,
        item: str,
        gate: str,
        ago: float,
        took: float,
        outcome: str = "passed",
        *,
        chunks: int = 1,
        started_ago: float | None = None,
    ):
        """A `ddflow review` that started ``ago`` seconds before NOW and ended ``took``
        seconds later, recording ``elapsed_s`` in its evidence as the command does.
        ``started_ago`` puts the `gate.started` earlier than the review itself (a review
        that was killed and re-run, or a gap the agent spent triaging)."""
        self.add("gate.started", item, NOW - (started_ago or ago), gate=gate)
        evidence = {
            "status": "REVIEWED",
            "elapsed_s": took,
            "chunks_total": chunks,
            "output_file": f".ddflow/local/reviews/{item}.{gate}.{ago}.jsonl",
        }
        self.add(f"gate.{outcome}", item, NOW - ago + took, gate=gate, evidence=evidence)

    def outcomes(self, gate: str, ago: float, passed: int, failed: int) -> None:
        for i in range(passed):
            self.add("gate.passed", f"i{i}", NOW - ago, gate=gate)
        for i in range(failed):
            self.add("gate.failed", f"f{i}", NOW - ago, gate=gate)


def _signals(log: Log, cfg: Config | None = None) -> FS.Signals:
    cfg = cfg or Config.load()
    return FS.compute(log.events, fold(log.events, strict=False), cfg, NOW)


def test_slow_recent_review_against_fast_baseline_gives_ratio_two():
    log = Log()
    for i in range(20):  # baseline: 20 reviews over the previous days, 100 s each
        log.review(f"b{i}", "critic", 2 * DAY + i * 600, 100)
    for i in range(5):  # recent: 5 reviews in the last 30 minutes, 200 s each
        log.review(f"r{i}", "rubber_duck", 5 * MIN + i * 60, 200)
    assert _signals(log).reviewer_latency_ratio == 2.0


def test_too_few_samples_give_none():
    log = Log()
    for i in range(20):
        log.review(f"b{i}", "critic", 2 * DAY + i * 600, 100)
    for i in range(4):  # one under the recent minimum
        log.review(f"r{i}", "critic", 5 * MIN + i * 60, 200)
    assert _signals(log).reviewer_latency_ratio is None
    log2 = Log()
    for i in range(19):  # one under the baseline minimum
        log2.review(f"b{i}", "critic", 2 * DAY + i * 600, 100)
    for i in range(5):
        log2.review(f"r{i}", "critic", 5 * MIN + i * 60, 200)
    assert _signals(log2).reviewer_latency_ratio is None


def test_latency_ignores_non_review_gates_and_unpaired_outcomes():
    log = Log()
    for i in range(20):
        log.review(f"b{i}", "critic", 2 * DAY + i * 600, 100)
    for i in range(5):
        log.review(f"r{i}", "critic", 5 * MIN + i * 60, 200)
        log.review(f"u{i}", "unit_tests", 5 * MIN + i * 60, 9000)  # not a review gate
    log.add("gate.passed", "orphan", NOW - 60, gate="critic")  # no start: no sample
    assert _signals(log).reviewer_latency_ratio == 2.0


def test_half_failed_outcomes_give_rate_half_and_few_outcomes_none():
    log = Log()
    log.outcomes("unit_tests", 10 * MIN, passed=5, failed=5)
    assert _signals(log).gate_failure_rate == 0.5
    log = Log()
    log.outcomes("unit_tests", 10 * MIN, passed=4, failed=5)  # 9 outcomes
    assert _signals(log).gate_failure_rate is None


def test_gate_failure_rate_window_is_sixty_minutes():
    log = Log()
    log.outcomes("unit_tests", 10 * MIN, passed=10, failed=0)
    log.outcomes("unit_tests", 90 * MIN, passed=0, failed=50)  # outside the window
    assert _signals(log).gate_failure_rate == 0.0


def test_merge_failure_rate_over_two_hours():
    log = Log()
    log.outcomes("merge", 100 * MIN, passed=3, failed=1)
    log.outcomes("merge", 3 * 60 * MIN, passed=0, failed=9)  # outside
    log.outcomes("unit_tests", 10 * MIN, passed=0, failed=9)  # not a merge gate
    assert _signals(log).merge_failure_rate == 0.25
    assert _signals(Log()).merge_failure_rate is None


def test_independent_ready_equals_what_the_offer_produces():
    log = Log()
    log.add("phase.added", "P", title="p")
    for tid, prio, globs in (("A", 1, ["a.py"]), ("B", 2, ["a.py"]), ("C", 3, ["c.py"])):
        log.add("task.added", tid, parent="P", priority=prio, globs=globs)
    cfg = Config.load()
    state = fold(log.events, strict=False)
    cfg.schedule.max_parallel_tasks = 2
    cfg.worktree.max_parallel = 99
    offered = plan(state, cfg, now=NOW).ready
    assert FS.compute(log.events, state, cfg, NOW).independent_ready == len(offered) == 2
    # The cap does not shrink the signal: it is the work that COULD run in parallel.
    cfg.schedule.max_parallel_tasks = 1
    assert FS.compute(log.events, state, cfg, NOW).independent_ready == 2
    assert cfg.schedule.max_parallel_tasks == 1  # the caller's config is untouched


def test_independent_ready_sits_on_top_of_the_in_flight_set():
    log = Log()
    log.add("phase.added", "P", title="p")
    for tid, globs in (("A", ["a.py"]), ("B", ["a.py"]), ("C", ["c.py"])):
        log.add("task.added", tid, parent="P", globs=globs)
    lease = {"holder": "other", "at": NOW, "ttl_s": 3600, "globs": ["a.py"]}
    log.events.append(
        Event(kind="lease.acquired", subject="A", data=lease, lamport=99, ts=_ts(NOW))
    )
    assert _signals(log).independent_ready == 1  # B overlaps the held A; only C


def test_loop_findings_counts_the_loops_detector():
    log = Log()
    log.add("phase.added", "P", title="p")
    log.add("task.added", "A", parent="P", needs=["B"])
    log.add("task.added", "B", parent="P", needs=["A"])
    cfg = Config.load()
    state = fold(log.events, strict=False)
    from ddflow.core import progress as PR

    assert FS.compute(log.events, state, cfg, NOW).loop_findings == len(
        PR.detect(log.events, state, cfg)
    )
    assert _signals(log).loop_findings >= 1


def test_a_log_with_no_gate_events_is_all_none_and_never_raises():
    s = _signals(Log())
    assert s.reviewer_latency_ratio is None
    assert s.gate_failure_rate is None
    assert s.merge_failure_rate is None
    assert s.loop_findings == 0
    assert s.independent_ready == 0
    assert s.as_signals()["gate_failure_rate"] is None


def test_unparseable_timestamps_and_zero_baseline_do_not_raise():
    log = Log()
    log.events.append(Event(kind="gate.started", subject="x", data={"gate": "critic"}, ts="junk"))
    log.events.append(Event(kind="gate.passed", subject="x", data={"gate": "critic"}))
    for i in range(20):
        log.review(f"b{i}", "critic", 2 * DAY + i * 600, 0)  # zero-length baseline
    for i in range(5):
        log.review(f"r{i}", "critic", 5 * MIN + i * 60, 10)
    assert _signals(log).reviewer_latency_ratio is None


def test_no_new_config_keys_are_needed():
    """Defaults live in the module; a config that knows nothing of these signals works."""
    assert FS.RECENT_REVIEW_S == 30 * MIN and FS.BASELINE_S == 7 * DAY
    assert FS.GATE_WINDOW_S == 60 * MIN and FS.MERGE_WINDOW_S == 120 * MIN


def test_history_notes_name_each_signal_lacking_history_and_nothing_else():
    notes = FS.history_notes(_signals(Log()))
    assert len(notes) == 3 and all("neutral" in n for n in notes)
    assert FS.history_notes(FS.Signals(0.5, 0.1, 0.0, gate_failure_ratio=1.0)) == []


def test_a_short_gate_baseline_is_noted_for_the_ratio():
    """The last hour has outcomes but the week before has too few: only the ratio is out."""
    notes = FS.history_notes(FS.Signals(0.5, 0.1, 0.0, gate_failure_ratio=None))
    assert len(notes) == 1 and notes[0].startswith("gate_failure_ratio: under 20")


# -- bug B1c5dbe3103: the ratio measures the reviewer, not the agent or the diff ----------


def _baseline(log: Log, took: float = 100) -> None:
    for i in range(20):  # 20 reviews over the previous days
        log.review(f"b{i}", "critic", 2 * DAY + i * 600, took)


def _busy(log: Log, took: float, **kw: int) -> None:
    """5 recent reviews a minute apart, with 3 more running beside each: a busy reviewer."""
    for i in range(5):
        log.review(f"r{i}", "critic", 25 * MIN - i * 60, took, **kw)
        for j in range(3):
            log.review(f"c{i}{j}", "rubber_duck", 25 * MIN - i * 60 - 5, took, **kw)


def test_a_large_diff_at_the_same_service_time_does_not_raise_the_ratio():
    log = Log()
    _baseline(log)
    # 40 chunks go out in 3 waves of 16: 300 s of wall time is 100 s per request
    _busy(log, 300, chunks=40)
    assert _signals(log).reviewer_latency_ratio == pytest.approx(1.0)


def test_slow_answers_from_an_idle_reviewer_are_not_saturation():
    log = Log()
    _baseline(log)
    for i in range(5):  # three times slower, but one review at a time
        log.review(f"r{i}", "critic", 25 * MIN - i * 300, 300)
    assert _signals(log).reviewer_latency_ratio is None


def test_slow_answers_from_a_busy_reviewer_are():
    log = Log()
    _baseline(log)
    _busy(log, 300)
    assert _signals(log).reviewer_latency_ratio == pytest.approx(3.0)


def test_a_triage_gap_before_a_hand_recorded_outcome_is_not_a_sample():
    log = Log()
    _baseline(log)
    _busy(log, 100)
    for i in range(5):
        # A review killed after gate.started, then `gate record` 20 minutes later, after
        # triage: that gap is the agent's, not the reviewer's ...
        log.add("gate.started", f"m{i}", NOW - 20 * MIN, gate="critic")
        log.add("gate.passed", f"m{i}", NOW - 60, gate="critic", evidence={"note": "by hand"})
        # ... and a `gate record` that carries the last review's evidence along is that
        # same review again, not a second sample of it.
        review = next(e for e in log.events if e.subject == f"r{i}" and e.kind == "gate.passed")
        log.add("gate.passed", f"r{i}", NOW - 30, gate="critic", evidence=review.data["evidence"])
    assert _signals(log).reviewer_latency_ratio == 1.0
    samples = FS._review_samples(log.events)
    assert len(samples) == 20 + 5 * 4  # the baseline, and each busy review once
