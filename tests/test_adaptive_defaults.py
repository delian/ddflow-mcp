"""The shipped marks for the adaptive-flow signals beyond load (D-unify 8, B-af-default-thresholds).

Free memory under 15% shrinks the limit and under 5% pauses admission; free disk under 10%
shrinks and under 3% pauses; reviewer latency and the gate failure rate shrink it at twice
their own measured baseline. Driven through the real sampling path with a scripted signal
source and an injected clock, so nothing depends on this machine; plus the strictest
fallback a config file gets for a `[schedule.signals]` table that is not valid.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ddflow.config import FLOW_SIGNALS, Config, _signals_problem, default_signals
from ddflow.config_sections.schedule import SHIPPED_MARKS, strictest_signals
from ddflow.core import flowparams as FP
from ddflow.core import flowsignals as FS
from ddflow.core.events import Event
from ddflow.core.model import State
from ddflow.infra import signals as SIG
from ddflow.services import flowstate as F

T0 = 1_800_000_000.0
MIN = 60.0
DAY = 86400.0
#: Healthy on every host signal: load per core 0.05, 60% memory free, 50% disk free.
HEALTHY = {"load": 0.05, "memory_free_frac": 0.6, "disk_free_frac": 0.5}


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _limit(repo: Path, values: dict, events: list[Event] | None = None, n: int = 10):
    """Sample ``values`` ``n`` times a minute apart, then fold: the decision in force."""
    clock, src = Clock(), SIG.FakeSource(values)
    ctx = F.FlowCtx(repo=repo, cfg=Config(), state=State(), events=lambda: events or [])
    for _ in range(n):
        F.sample_if_due(ctx, src, clock)
        clock.t += 60
    return F.current_limit(ctx, None, clock)


def test_the_shipped_marks_are_d_unify_8():
    sig = Config().schedule.signals
    assert sig["memory_pressure"] == {"low": 0.75, "high": 0.85, "critical": 0.95}
    assert sig["disk_pressure"] == {"low": 0.85, "high": 0.90, "critical": 0.97}
    assert sig["reviewer_latency_ratio"] == {"low": 1.5, "high": 2.0}
    assert sig["gate_failure_ratio"] == {"low": 1.5, "high": 2.0}
    assert sig["load_per_core"] == {"low": 0.15, "high": 0.75}  # unchanged
    assert set(sig["enabled"]) == set(FLOW_SIGNALS)
    assert _signals_problem(default_signals()) == ""
    assert set(FP.thresholds(Config())) == set(SHIPPED_MARKS)


def test_healthy_host_holds_the_start_value(repo):
    d = _limit(repo, HEALTHY)
    assert (d.limit, d.admit_paused) == (4, False)


@pytest.mark.parametrize(
    "values,signal",
    [
        ({**HEALTHY, "memory_free_frac": 0.12}, "memory_pressure"),  # under 15% free
        ({**HEALTHY, "disk_free_frac": 0.08}, "disk_pressure"),  # under 10% free
    ],
)
def test_low_free_memory_or_disk_shrinks_the_limit(repo, values, signal):
    d = _limit(repo, values)
    assert d.limit < 4 and d.limited_by == signal, d
    assert not d.admit_paused


@pytest.mark.parametrize(
    "values,signal",
    [
        ({**HEALTHY, "memory_free_frac": 0.04}, "memory_pressure"),  # under 5% free
        ({**HEALTHY, "disk_free_frac": 0.02}, "disk_pressure"),  # under 3% free
    ],
)
def test_almost_no_free_memory_or_disk_pauses_admission(repo, values, signal):
    d = _limit(repo, values)
    assert d.admit_paused and signal in d.reason, d


@pytest.mark.parametrize(
    "values",
    [
        {**HEALTHY, "memory_free_frac": 0.16},  # just above the shrink mark
        {**HEALTHY, "disk_free_frac": 0.11},
    ],
)
def test_just_above_the_marks_does_not_shrink(repo, values):
    assert _limit(repo, values).limit == 4


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _outcomes(at: float, passed: int, failed: int) -> list[Event]:
    kinds = ["gate.passed"] * passed + ["gate.failed"] * failed
    return [
        Event(kind=k, subject=f"i{i}", data={"gate": "unit_tests"}, lamport=i + 1, ts=_ts(at))
        for i, k in enumerate(kinds)
    ]


def _reviews(at: float, n: int, took: float, first: int) -> list[Event]:
    out = []
    for i in range(n):
        start = at + i * 60
        data = {"gate": "critic"}
        lam = first + 2 * i
        out.append(
            Event(kind="gate.started", subject=f"r{lam}", data=data, lamport=lam, ts=_ts(start))
        )
        out.append(
            Event(
                kind="gate.passed",
                subject=f"r{lam}",
                data=data,
                lamport=lam + 1,
                ts=_ts(start + took),
            )
        )
    return out


def test_gate_failure_ratio_is_the_recent_rate_over_the_baseline_rate():
    # baseline: 40 outcomes two days ago, 20% failed; recent: 10 in the last hour, 50% failed
    events = _outcomes(T0 - 2 * DAY, passed=32, failed=8) + _outcomes(T0 - 10 * MIN, 5, 5)
    assert FS.gate_failure_ratio(events, T0) == pytest.approx(2.5)
    assert FS.gate_failure_rate(events, T0) == 0.5  # the raw rate is unchanged


@pytest.mark.parametrize(
    "events",
    [
        _outcomes(T0 - 2 * DAY, 40, 0) + _outcomes(T0 - 10 * MIN, 5, 5),  # a clean baseline
        _outcomes(T0 - 2 * DAY, 15, 4) + _outcomes(T0 - 10 * MIN, 5, 5),  # 19 baseline
        _outcomes(T0 - 2 * DAY, 32, 8) + _outcomes(T0 - 10 * MIN, 5, 4),  # 9 recent
        _outcomes(T0 - 9 * DAY, 32, 8) + _outcomes(T0 - 10 * MIN, 5, 5),  # baseline too old
    ],
)
def test_gate_failure_ratio_without_a_baseline_is_unavailable(events):
    assert FS.gate_failure_ratio(events, T0) is None


def test_the_baseline_ends_where_the_recent_hour_begins():
    # 50 failures 30 minutes ago belong to the recent window only, never to the baseline
    events = _outcomes(T0 - 2 * DAY, 32, 8) + _outcomes(T0 - 30 * MIN, 0, 50)
    assert FS.gate_failure_ratio(events, T0) == pytest.approx(1 / 0.2)


def test_failures_at_twice_the_baseline_shrink_the_limit(repo):
    # The log is read at fold time, so the samples' clock and the events' clock line up
    # at the last sample: T0 + 9 minutes.
    now = T0 + 9 * MIN
    events = _outcomes(now - 2 * DAY, 32, 8) + _outcomes(now - 5 * MIN, 4, 6)  # 3x
    d = _limit(repo, HEALTHY, events)
    assert d.limit < 4 and d.limited_by == "gate_failure_ratio", d


def test_reviews_at_twice_the_baseline_latency_shrink_the_limit(repo):
    now = T0 + 9 * MIN
    events = _reviews(now - 2 * DAY, 20, 100, 1) + _reviews(now - 25 * MIN, 5, 300, 100)
    assert FS.reviewer_latency_ratio(events, now) == pytest.approx(3.0)
    d = _limit(repo, HEALTHY, events)
    assert d.limit < 4 and d.limited_by == "reviewer_latency_ratio", d


def _write(repo: Path, committed: str = "", local: str = "") -> None:
    (repo / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(committed, "utf-8")
    (repo / ".ddflow" / "local" / "config.toml").write_text(local, "utf-8")


def test_a_bad_table_in_a_file_loads_with_the_strictest_fallback(repo):
    _write(
        repo,
        committed="[schedule.signals.load_per_core]\nhigh = 0.5\n",
        local="[schedule.signals]\nenabled = ['load_per_core']\n"
        "[schedule.signals.memory_pressure]\nlow = 0.99\nhigh = 0.98\n",
    )
    cfg = Config.load(repo)  # does not raise
    sig = cfg.schedule.signals
    assert set(sig["enabled"]) == set(FLOW_SIGNALS)  # the bad layer cannot switch any off
    assert sig["load_per_core"] == {"low": 0.15, "high": 0.5}  # the committed, stricter mark
    assert sig["memory_pressure"] == SHIPPED_MARKS["memory_pressure"]  # 0.99/0.98 not trusted
    assert cfg.sources["schedule.signals"].endswith("(strictest fallback)")
    assert any("schedule.signals" in n and "strictest" in n for n in cfg.unknown_knobs)
    assert _signals_problem(sig) == ""


def test_a_looser_valid_table_is_applied_as_written(repo):
    _write(repo, committed="[schedule.signals.memory_pressure]\nhigh = 0.9\n")
    sig = Config.load(repo).schedule.signals
    assert sig["memory_pressure"] == {"low": 0.75, "high": 0.9, "critical": 0.95}


@pytest.mark.parametrize("seed", range(6))
def test_the_strictest_fallback_is_always_valid_and_never_looser(seed):
    """Any valid base: the fallback validates, and each mark is at most the base's and the
    shipped one."""
    marks = [0.1, 0.5, 0.8, 0.9, 1.0, 3.0]
    combos = [c for c in itertools.combinations_with_replacement(marks, 3) if c[0] <= c[1] <= c[2]]
    base = default_signals()
    for name, (low, high, crit) in zip(FLOW_SIGNALS, combos[seed::6], strict=False):
        base[name] = {"low": low, "high": high, "critical": crit}
    assert _signals_problem(base) == "", base
    out = strictest_signals(base)
    assert _signals_problem(out) == "", out
    shipped = default_signals()
    for name, table in out.items():
        if name == "enabled":
            continue
        for mark, value in table.items():
            for source in (base.get(name, {}), shipped.get(name, {})):
                if mark in source:
                    assert value <= source[mark], (name, mark, value, source)
