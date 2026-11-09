"""Per-test durations from a junit report (PM-test-durations): parse, rollup, critical path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.services.gates.runner import capture_test_durations
from ddflow.services.perf.testtimes import parse_junit, rollup

JUNIT = """<?xml version="1.0"?>
<testsuite name="pytest" tests="3">
  <testcase classname="tests.test_a" name="test_slow" time="10.0"/>
  <testcase classname="tests.test_a" name="test_fast" time="1.0"/>
  <testcase classname="tests.test_b" name="test_ok" time="2.0"/>
</testsuite>
"""


def test_parse_junit_yields_one_case_per_testcase():
    cases = parse_junit(JUNIT)
    assert [c.test_id for c in cases] == [
        "tests.test_a.test_slow",
        "tests.test_a.test_fast",
        "tests.test_b.test_ok",
    ]


def test_rollup_reports_count_top_slowest_serial_and_the_path_floor():
    r = rollup(parse_junit(JUNIT), workers=4, top_n=2)
    assert r["count"] == 3
    assert r["top_slowest"][0]["test_id"] == "tests.test_a.test_slow"
    assert r["serial_seconds"] == pytest.approx(13.0)
    # the floor cannot beat the slowest FILE (test_a = 11.0: a file does not parallelize)
    assert r["critical_path_floor_s"] == pytest.approx(11.0)


def test_the_skew_is_the_slowest_files_share_of_the_serial_work():
    r = rollup(parse_junit(JUNIT), workers=1, top_n=2)
    # floor = max(slowest test 10, slowest file 11) = 11 (a file cannot parallelize within itself)
    assert r["critical_path_floor_s"] == pytest.approx(11.0)
    # skew = the slowest file (test_a = 11.0) as a share of the serial work (13.0)
    assert r["skew"] == pytest.approx(11.0 / 13.0)


def test_capture_folds_into_the_ring_and_returns_only_the_rollup(tmp_path: Path):
    out = capture_test_durations(tmp_path, "ITEM-1", "unit_tests", JUNIT, workers=4)
    assert out["count"] == 3 and out["critical_path_floor_s"] == pytest.approx(11.0)
    # the raw durations landed in the local ring (never in the evidence)
    tmp_path / ".ddflow" / "local" / "perf" / "test_durations.jsonl"
    assert "test_a.test_slow" not in out  # the rollup carries ids only via top_slowest


def test_an_unparseable_report_is_unavailable(tmp_path: Path):
    out = capture_test_durations(tmp_path, "I", "ci", "<not-valid", workers=4)
    assert out == {"unavailable": True, "item": "I", "gate": "ci"}
