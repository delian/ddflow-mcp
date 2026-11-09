"""Retention, compaction and redaction for the perf store (PM-retention)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.perf import retention as R
from ddflow.services.perf.store import PerfMetricsConfig, Sample, _append_line, append, ring_path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return tmp_path


def test_redaction_keeps_only_the_allowlist_and_names_the_dropped(repo: Path):
    kept, dropped = R.redact_row(
        {"t": 1.0, "env": "a", "value": 5.0, "gate": "g", "secret": "x", "prompt": "y"}
    )
    assert kept == {"t": 1.0, "env": "a", "value": 5.0, "gate": "g"}
    assert set(dropped) == {"secret", "prompt"}


def test_audit_reports_dropped_fields_and_bytes(repo: Path):
    append(repo, "rss", Sample(1.0, "a", 5.0, "g", "d"))
    _append_line(
        ring_path(repo, "rss"),
        json.dumps({"t": 2.0, "env": "a", "value": 6.0, "prompt": "should not be here"}),
    )
    a = R.audit(repo, "rss")
    assert a.rows == 2 and a.dropped == 1
    assert a.bytes > 0


def test_a_field_outside_the_allowlist_gets_a_doctor_note(repo: Path):
    append(repo, "rss", Sample(1.0, "a", 5.0, "g", "d"))
    _append_line(
        ring_path(repo, "rss"), json.dumps({"t": 2.0, "env": "a", "value": 6.0, "token": "x"})
    )
    notes = R.doctor(repo, PerfMetricsConfig(ring_max_bytes=10**6))
    assert any("outside the allowlist" in n for n in notes)


def test_a_ring_over_budget_gets_a_doctor_note(repo: Path):
    append(repo, "rss", Sample(1.0, "a", 5.0, "g", "d"))
    notes = R.doctor(repo, PerfMetricsConfig(ring_max_bytes=1))  # impossible budget
    assert any("budget" in n for n in notes)


def test_compaction_folds_aged_samples_into_the_commit_and_trims(repo: Path):
    append(repo, "rss", Sample(100.0, "a", 5.0, "g", "d"))
    append(repo, "rss", Sample(101.0, "a", 6.0, "g", "d"))
    cfg = PerfMetricsConfig(ring_max_age_s=100_000, ring_max_bytes=10**6)
    kept, _notes = R.compact(repo, "rss", 200_000.0, cfg=cfg)  # day 0 ended at 86400 < now
    assert kept == 0  # both day-0 samples aged out of the raw ring
    # ... and the day's aggregate was committed, so the p50 survives the ring loss
    from ddflow.services.perf.store import read_aggregates

    agg = read_aggregates(repo, "rss")
    assert agg and "a" in agg[0][1]


def test_an_incomplete_day_is_not_folded_into_an_aggregate(repo: Path):
    append(repo, "rss", Sample(100.0, "a", 5.0, "g", "d"))  # day 0, far from ending
    _kept, _notes = R.compact(
        repo, "rss", 1000.0, cfg=PerfMetricsConfig(ring_max_age_s=10**6, ring_max_bytes=10**6)
    )
    from ddflow.services.perf.store import read_aggregates

    assert read_aggregates(repo, "rss") == []  # day 0 is still open: no premature daily number


def test_compact_drops_a_non_allowlist_field_from_disk(repo: Path):
    append(repo, "rss", Sample(1.0, "a", 5.0, "g", "d"))
    _append_line(
        ring_path(repo, "rss"), json.dumps({"t": 2.0, "env": "a", "value": 6.0, "token": "x"})
    )
    _kept, notes = R.compact(
        repo, "rss", 1000.0, cfg=PerfMetricsConfig(ring_max_age_s=10**6, ring_max_bytes=10**6)
    )
    # the hostile `token` key is gone from the stored ring AND was doctor-noted
    stored = ring_path(repo, "rss").read_text("utf-8")
    assert "token" not in stored
    assert any("token" in n for n in notes)
