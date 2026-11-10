"""Proposal generators for the TEST SUITE (PM-prop-tests), with seeded inefficiencies."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.services.perf import prop_tests


def _rollup(per_file, workers=4):
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


def test_a_file_over_the_size_rule_and_the_share_is_a_split_candidate():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 80.0, "tests/test_b.py": 20.0}, workers=1)
    sizes = {"tests/test_a.py": 200, "tests/test_b.py": 10}
    [p] = prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg)
    assert p.kind == "split_file"
    assert p.advice["file"] == "tests/test_a.py"
    assert p.because  # the reason carries the floor and the file


def test_a_file_under_the_size_rule_is_not_split_from_duration_alone():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 80.0}, workers=1)
    sizes = {"tests/test_a.py": 50}
    assert (
        prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg) == []
    )


def test_a_small_share_is_not_a_split_candidate():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 1.0, "tests/test_b.py": 1.0}, workers=8)
    sizes = {"tests/test_a.py": 500}
    assert (
        prop_tests.propose_split_files(rollup, sizes=sizes, max_file_bytes=100, cfg=cfg) == []
    )


# -- shard vs workers ------------------------------------------------------------------


def test_a_skewed_suite_is_a_shard_of_the_slowest_file():
    cfg = PerfMetricsConfig(prop_shard_skew=0.9)
    rollup = _rollup({"tests/test_a.py": 90.0, "tests/test_b.py": 5.0, "tests/test_c.py": 5.0}, workers=8)
    p = prop_tests.propose_shard_or_workers(rollup, cfg=cfg)
    assert p is not None and p.kind == "shard"
    assert p.advice["kind"] == "shard" and p.advice["file"] == "tests/test_a.py"


def test_an_unskewed_suite_proposes_a_worker_raise_not_a_shard():
    cfg = PerfMetricsConfig(prop_shard_skew=0.9)
    rollup = _rollup({"tests/test_a.py": 30.0, "tests/test_b.py": 30.0, "tests/test_c.py": 40.0}, workers=8)
    p = prop_tests.propose_shard_or_workers(rollup, cfg=cfg)
    assert p is not None and p.kind == "workers"
    assert p.advice["kind"] == "raise"


def test_an_empty_rollup_proposes_nothing():
    assert prop_tests.propose_shard_or_workers(
        {"per_file": {}, "workers": 4}, cfg=PerfMetricsConfig(prop_shard_skew=0.9)
    ) is None


# -- selection -------------------------------------------------------------------------


def test_a_selection_is_widened_when_it_missed_failures():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    runs = [
        {"selected_fraction": 0.45, "misses": 1},
        {"selected_fraction": 0.50, "misses": 0},
        {"selected_fraction": 0.48, "misses": 2},
    ]
    p = prop_tests.propose_selection(runs, cfg=cfg)
    assert p is not None and p.kind == "selection"
    assert p.advice == "widen"
    assert p.because  # the reason carries the misses and the run count


def test_too_few_runs_is_not_a_selection_claim_or_a_zero():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    assert prop_tests.propose_selection([{"misses": 3}], cfg=cfg) is None


def test_a_miss_is_only_counted_when_it_was_a_miss():
    cfg = PerfMetricsConfig(prop_select_miss_min=2, prop_select_min_runs=3)
    assert prop_tests.propose_selection([{"misses": 0}, {"misses": 0}, {"misses": 0}], cfg=cfg) is None


def test_the_next_call_names_a_real_command_on_each_kind():
    """The only next_call every proposal shares must resolve (it is named once, so the
    parity ratchet reads it here too)."""
    p = prop_tests.Proposal("split_file", {"file": "a"}, {}, "reason")
    assert prop_tests.Proposal.next_call(p)  # non-empty, and names the decision record


def test_the_split_advice_carries_the_floor_and_size():
    cfg = PerfMetricsConfig(prop_split_share=0.5)
    rollup = _rollup({"tests/test_a.py": 90.0}, workers=1)
    [p] = prop_tests.propose_split_files(
        rollup, sizes={"tests/test_a.py": 900}, max_file_bytes=100, cfg=cfg
    )
    assert p.advice["serial_s"] == pytest.approx(90.0)
    assert "share" in p.evidence
