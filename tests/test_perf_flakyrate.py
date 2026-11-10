"""Flaky-rate view and quarantine proposals (PM-flaky): window, rates, retry cost, proposals."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.core import clock
from ddflow.services.perf import flakyrate

DAY = 86400
CFG = PerfMetricsConfig(
    flaky_window_s=7 * DAY,
    flaky_quarantine_after=3,
    flaky_quarantine_expiry_s=30 * DAY,
    flaky_retry_cost_s=60.0,
)
NOW = clock.epoch("2026-11-01T00:00:00Z")


def _row(test: str, t: float, item: str = "", gate: str = "unit_tests") -> dict:
    return {
        "test": test,
        "at": clock.iso_at(t),
        "item": item,
        "gate": gate,
        "commit": "abc1234",
        "load": 1.0,
        "how": "the failed tests passed on a rerun",
        "failure": "AssertionError: boom",
    }


def test_file_of_cuts_a_pytest_node_and_maps_a_unittest_class():
    assert flakyrate.file_of("tests/test_a.py::test_thing[1]") == "tests/test_a.py"
    assert flakyrate.file_of("tests/test_a.py") == "tests/test_a.py"
    assert flakyrate.file_of("tests.test_a.TestSuite.test_thing") == "tests/test_a.py"


def test_window_rows_excludes_a_flake_outside_the_window():
    rows = [_row("tests/a.py::t1", NOW - DAY), _row("tests/a.py::t2", NOW - 100 * DAY)]
    assert [r["test"] for r in flakyrate.window_rows(rows, NOW, 7 * DAY)] == ["tests/a.py::t1"]


def test_rates_report_count_rate_per_day_and_retry_cost():
    rows = [
        _row("tests/a.py::t1", NOW - DAY),
        _row("tests/a.py::t1", NOW - 2 * DAY),
        _row("tests/a.py::t2", NOW - DAY),
    ]
    view = flakyrate.rates(rows, now=NOW, perf_metrics=CFG)
    assert view["flakes"] == 3
    top = view["per_test"][0]
    assert top["test"] == "tests/a.py::t1"
    assert top["file"] == "tests/a.py"
    assert top["flakes"] == 2
    assert top["rate_per_day"] == pytest.approx(2 / 7)
    assert top["last_at"] == pytest.approx(NOW - DAY)
    # 3 flakes at 60s each in this window
    assert view["retry_cost_s"] == pytest.approx(3 * 60.0)


def test_rates_group_per_file_and_keep_ids_only():
    rows = [
        _row("tests/a.py::t1", NOW - DAY),
        _row("tests/a.py::t2", NOW - DAY),
        _row("tests/b.py::t3", NOW - DAY),
    ]
    view = flakyrate.rates(rows, now=NOW, perf_metrics=CFG)
    by_file = {p["file"]: p for p in view["per_file"]}
    assert by_file["tests/a.py"]["flakes"] == 2 and by_file["tests/a.py"]["tests"] == 2
    assert by_file["tests/b.py"]["flakes"] == 1
    # ids and counts only: no failure text rides out
    assert "failure" not in view["per_test"][0]


def test_proposal_fires_only_at_the_quarantine_threshold():
    two = [_row("tests/a.py::t1", NOW - i * DAY) for i in range(2)]
    assert flakyrate.proposals(two, now=NOW, perf_metrics=CFG) == []
    three = [_row("tests/a.py::t1", NOW - i * DAY) for i in range(3)]
    props = flakyrate.proposals(three, now=NOW, perf_metrics=CFG)
    assert len(props) == 1
    assert props[0]["test"] == "tests/a.py::t1"
    assert props[0]["flakes"] == 3
    assert props[0]["proposed"] == "quarantine"


def test_proposal_names_owner_and_expiry():
    rows = [_row("tests/a.py::t1", NOW - i * DAY, item="ITEM-9") for i in range(3)]
    prop = flakyrate.proposals(rows, now=NOW, perf_metrics=CFG)[0]
    assert prop["owner"] == "ITEM-9"
    assert prop["expires_at"] == clock.iso_at(NOW + 30 * DAY)
    assert prop["file"] == "tests/a.py"


def test_a_flake_outside_the_window_never_proposes():
    rows = [
        _row("tests/a.py::t1", NOW - 100 * DAY),
        _row("tests/a.py::t1", NOW - 101 * DAY),
        _row("tests/a.py::t1", NOW - 102 * DAY),
    ]
    assert flakyrate.proposals(rows, now=NOW, perf_metrics=CFG) == []


def test_report_reads_the_flake_log_and_returns_rates_and_proposals(tmp_path: Path):
    log = tmp_path / ".ddflow" / "local" / "flakes.jsonl"
    log.parent.mkdir(parents=True)
    rows = [_row("tests/a.py::t1", NOW - i * DAY, item="ITEM-1") for i in range(3)]
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    view = flakyrate.report(tmp_path, now=NOW, perf_metrics=CFG)
    assert view["flakes"] == 3
    assert [p["test"] for p in view["proposals"]] == ["tests/a.py::t1"]
    # a missing log (no file) is an empty view, not an error
    assert flakyrate.report(tmp_path / "missing", now=NOW, perf_metrics=CFG)["flakes"] == 0
