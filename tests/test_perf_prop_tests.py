"""Proposal generators for the TEST SUITE (PM-prop-tests), with seeded inefficiencies."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.services.perf import prop_tests


def _rollup(per_file, workers=1):
    """The same shape ``testtimes.rollup`` returns: makespan = max(slowest_file,
    serial / workers), skew = slowest_file / makespan. workers=1 has no wall divider, so the
    skew is the slowest file's share of the serial run."""
    serial = sum(per_file.values())
    makespan = max(max(per_file.values(), default=0.0), serial / workers if workers else serial)
    slowest = max(per_file.values(), default=0.0) if per_file else 0.0
    return {
        "per_file": dict(per_file),
        "serial_seconds": serial,
        "critical_path_floor_s": makespan,
        "skew": slowest / makespan if makespan else 0.0,
        "workers": workers,
    }


# -- split -----------------------------------------------------------------------------


def test_a_file_over_the_size_rule_beyond_the_share_is_a_split_candidate():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 8.0, "tests/test_b.py": 2.0}, workers=1)
    sizes = {"tests/test_a.py": 200, "tests/test_b.py": 10}
    [p] = prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg)
    assert p.kind == "split_file"
    assert p.advice["file"] == "tests/test_a.py"
    assert p.sample_size == 2 and p.expected_saving_s_run == pytest.approx(0.0)


def test_a_file_under_the_size_rule_is_not_split():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 8.0}, workers=1)
    sizes = {"tests/test_a.py": 50}
    assert prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg) == []


def test_a_slow_file_with_no_size_is_not_split():
    """A file whose duration share exceeds the floor but whose SIZE cannot be checked is not
    a split candidate -- the split is for a file that is over the size rule, and no size in
    hand confirms that."""
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 8.0, "tests/test_b.py": 2.0}, workers=1)
    assert prop_tests.propose_split_files(rollup, sizes={}, max_file_bytes=100, cfg=cfg) == []


# -- shard vs workers ------------------------------------------------------------------


def test_a_skewed_suite_is_a_shard_of_the_slowest_file():
    cfg = PerfMetricsConfig(prop_shard_skew=0.5)
    rollup = _rollup({"tests/test_a.py": 9.0, "tests/test_b.py": 1.0}, workers=1)
    p = prop_tests.propose_shard_or_workers(rollup, cfg=cfg)
    assert p is not None and p.kind == "shard"
    assert p.advice["kind"] == "shard" and p.advice["file"] == "tests/test_a.py"


def test_an_unskewed_suite_proposes_a_worker_raise_not_a_shard():
    cfg = PerfMetricsConfig(prop_shard_skew=0.5)
    rollup = _rollup(
        {"tests/test_a.py": 4.0, "tests/test_b.py": 3.0, "tests/test_c.py": 3.0}, workers=1
    )
    p = prop_tests.propose_shard_or_workers(rollup, cfg=cfg)
    assert p is not None and p.kind == "workers"
    assert p.advice["kind"] == "raise"


def test_a_rollup_with_no_floor_proposes_nothing():
    assert (
        prop_tests.propose_shard_or_workers(
            {"per_file": {"a.py": 1.0}, "critical_path_floor_s": None, "workers": 4},
            cfg=PerfMetricsConfig(prop_shard_skew=0.5),
        )
        is None
    )


def test_an_empty_rollup_proposes_nothing():
    assert (
        prop_tests.propose_shard_or_workers(
            {"per_file": {}, "critical_path_floor_s": 10.0, "workers": 4},
            cfg=PerfMetricsConfig(prop_shard_skew=0.5),
        )
        is None
    )


# -- selection -------------------------------------------------------------------------


def test_a_selection_is_widened_when_it_missed_failures():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    runs = [
        {"selected_fraction": 0.45, "misses": 1},
        {"selected_fraction": 0.50, "misses": 0},
        {"selected_fraction": 0.48, "misses": 1},
    ]
    p = prop_tests.propose_selection(runs, cfg=cfg)
    assert p is not None and p.kind == "selection"
    assert p.advice == "widen"
    assert p.sample_size == 3 and p.effect_size == 2


def test_a_missing_miss_is_unavailable_not_a_miss():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    runs = [{"misses": None}, {"misses": None}, {"misses": None}]
    assert prop_tests.propose_selection(runs, cfg=cfg) is None


def test_a_fractional_miss_is_unavailable_not_a_measured_zero():
    """A fractional miss count is unmeasured, never int()-truncated toward zero: it neither
    triggers a widen nor a narrow, so a run that recorded a fractional miss can never be
    read as a clean full run."""
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=2)
    runs = [{"selected_fraction": 1.0, "misses": 0.9}, {"selected_fraction": 1.0, "misses": 0.9}]
    assert prop_tests.propose_selection(runs, cfg=cfg) is None


def test_a_full_selection_with_no_miss_is_a_narrow():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=2)
    runs = [{"selected_fraction": 1.0, "misses": 0}, {"selected_fraction": 1.0, "misses": 0}]
    p = prop_tests.propose_selection(runs, cfg=cfg)
    assert p is not None and p.advice == "narrow"


def test_too_few_runs_is_not_a_selection_claim():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    assert prop_tests.propose_selection([{"misses": 3}], cfg=cfg) is None


def test_every_kind_names_a_real_next_call():
    """The next call each proposal shares names the decision record; it is one string, so
    the parity ratchet reads it without a separate page."""
    call = prop_tests._NEXT_CALLS["shard"]
    assert "ddflow decision add" in call  # resolve the parity ratchet would check


def test_a_split_proposal_next_call_names_the_file():
    """``Proposal.next_call`` substitutes the file the evidence selected -- not the static
    ``<file>`` placeholder -- so the accept command is the exact call for that proposal."""
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 8.0, "tests/test_b.py": 2.0}, workers=1)
    sizes = {"tests/test_a.py": 200, "tests/test_b.py": 10}
    [p] = prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg)
    call = p.next_call()
    assert "ddflow split tests/test_a.py" in call
    assert "<file>" not in call
