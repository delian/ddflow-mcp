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
    # workers=4: floor = max(slowest file 11, serial/4=3.25) = 11
    assert r["critical_path_floor_s"] == pytest.approx(11.0)


def test_the_skew_is_the_slowest_files_share_of_the_path():
    r = rollup(parse_junit(JUNIT), workers=1, top_n=2)
    # workers=1 cannot finish before the whole serial run (13.0): the floor is 13
    assert r["critical_path_floor_s"] == pytest.approx(13.0)
    assert r["skew"] == pytest.approx(11.0 / 13.0)


def test_capture_folds_into_the_ring_and_returns_only_the_rollup(tmp_path: Path):
    out = capture_test_durations(tmp_path, "ITEM-1", "unit_tests", JUNIT, workers=4)
    assert out["count"] == 3 and out["critical_path_floor_s"] == pytest.approx(11.0)
    # the raw durations landed in the LOCAL ring (read it back, don't just compute the path)
    ring = tmp_path / ".ddflow" / "local" / "perf" / "test_durations.jsonl"
    assert ring.exists() and ring.read_text().strip() != ""
    assert "test_a.test_slow" not in out  # the rollup carries ids only via top_slowest


def test_a_parameterized_test_id_strips_the_argument(tmp_path: Path):
    xml = '<testsuite name="pytest"><testcase classname="tests.test_a" name="test_x[secret-arg]" time="1.0"/></testsuite>'
    cases = parse_junit(xml)
    assert cases[0].test_id == "tests.test_a.test_x"  # the [secret-arg] is gone


def test_an_unparseable_report_is_unavailable(tmp_path: Path):
    out = capture_test_durations(tmp_path, "I", "ci", "<not-valid", workers=4)
    assert out == {"unavailable": True, "item": "I", "gate": "ci"}


def test_a_class_method_maps_to_its_module_file_not_a_split():
    xml = """<testsuite name="pytest" tests="2">
      <testcase classname="tests.test_a.TestSuite" name="test_slow" time="10"/>
      <testcase classname="tests.test_a.TestSuite" name="test_other" time="11"/>
    </testsuite>"""
    cases = parse_junit(xml)
    assert {c.file for c in cases} == {"tests/test_a.py"}
