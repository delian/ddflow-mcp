"""The regression and drift detector (PM-detect): a noisy flat series must not alert, a
seeded step must."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core import perfdetect as PD

FLAT_BASELINE = [100.0 + (i % 3) for i in range(12)]  # noisy around 100, no trend
FLAT_WINDOW = [101.0 - (i % 3) for i in range(12)]


def test_a_noisy_flat_series_does_not_alert():
    f = PD.detect_series("rss", "env:a", FLAT_BASELINE, FLAT_WINDOW, 0, 1)
    assert f is None


def test_a_seeded_step_above_both_thresholds_alerts():
    step = [123.0 + (i % 4) for i in range(12)]  # ~23% and ~23 s above the baseline
    f = PD.detect_series("rss", "env:a", FLAT_BASELINE, step, 0, 1)
    assert f is not None
    assert f.series == "rss" and f.effect_size == pytest.approx(0.23, abs=0.02)
    assert f.sample_size == 12


def test_a_step_under_both_thresholds_does_not_alert():
    step = [104.0] * 12  # +4% < both the 20% step and the 10% creep bar
    f = PD.detect_series("rss", "env:a", FLAT_BASELINE, step, 0, 1)
    assert f is None


def test_below_the_sample_floor_is_none_not_a_pass():
    f = PD.detect_series("rss", "env:a", [100.0, 101.0], [120.0, 121.0], 0, 1)
    assert f is None


def test_median_shift_reports_the_robust_shift():
    abs_s, rel, z = PD.median_shift([100.0] * 10, [120.0] * 10)
    assert abs_s == pytest.approx(20.0) and rel == pytest.approx(0.20) and z > 0


def test_creep_the_ewma_catches_a_slow_slide_a_fixed_threshold_misses():
    # A slow drift 100 -> 130 over 16 samples: the median step is ~15% (under the 20% step
    # threshold) but the EWMA crest crosses it, so the slow slide still alerts.
    from ddflow.config_sections.perf_metrics import PerfMetricsConfig

    slide = [100.0 + 2.0 * i for i in range(16)]
    cfg = PerfMetricsConfig(detect_creep_alpha=0.3)
    f = PD.detect_series("rss", "env:a", [100.0] * 12, slide, 0, 1, cfg=cfg)
    assert f is not None  # the EWMA crest crosses the threshold


def test_burn_rate_reports_short_long_and_budget_left():
    r = PD.burn_rate([5.0] * 10, [4.0] * 20, budget_s=20.0)
    assert r["short_p90"] == 5.0 and r["long_p90"] == 4.0 and r["budget_left_short"] == 15.0
