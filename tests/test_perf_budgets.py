"""Time budgets and the shrink-only ratchet (PM-budgets): breach vs None, severity, next call."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.services.perf import budgets


def test_a_series_under_budget_is_not_a_breach():
    cfg = PerfMetricsConfig(budget_gate_p90_s=180.0, budget_breach_min_samples=3)
    assert budgets.check("gate", [10.0] * 12, cfg=cfg) is None


def test_a_series_over_budget_is_a_breach_naming_the_budget():
    cfg = PerfMetricsConfig(budget_gate_p90_s=60.0, budget_breach_min_samples=3)
    b = budgets.check("gate", [70.0] * 12, cfg=cfg)
    assert b is not None
    assert b.budget == "gate" and b.kind == "gate"
    assert b.budget_s == pytest.approx(60.0) and b.n == 12
    assert b.p90_s >= 60.0


def test_below_the_sample_floor_is_none_not_a_false_clear():
    # two samples over budget are far below the floor: not a breach and not a zero
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_breach_min_samples=5)
    assert budgets.check("test_file", [99.0, 99.0], cfg=cfg) is None


def test_default_severity_is_warn_a_finding_not_a_refusal():
    cfg = PerfMetricsConfig(budget_gate_p90_s=60.0)
    b = budgets.check("gate", [99.0] * 10, cfg=cfg)
    assert b is not None and b.severity == "warn"
    assert b.blocks is False


def test_severity_block_is_a_refusal_naming_the_next_call():
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_severity="block")
    b = budgets.check("test_file", [99.0] * 10, cfg=cfg)
    assert b is not None and b.blocks is True
    text = b.next_call()
    assert "ddflow tests --full" in text  # CLI form
    assert "MCP:" in text  # MCP form named too (r-tell-dont-discover)


def test_a_stage_budget_breach_guides_raise_with_a_reason_only():
    cfg = PerfMetricsConfig(budget_stage_lease_wait_s=30.0, budget_severity="block")
    b = budgets.check("stage:lease", [99.0] * 10, cfg=cfg)
    assert b is not None and b.kind == "stage"
    assert "ddflow decision add" in b.next_call()  # raising needs a recorded reason


def test_the_ratchet_only_tightens():
    # a floor of 120 s only moves DOWN on its own
    floor, note = budgets.ratchet(120.0, 90.0)
    assert floor == pytest.approx(90.0) and "tighten" in note


def test_raising_the_floor_without_a_reason_is_refused():
    floor, note = budgets.ratchet(90.0, 120.0)
    assert floor == pytest.approx(90.0)  # unchanged, never silently raised
    assert "refused" in note


def test_raising_the_floor_with_a_recorded_reason_is_allowed():
    floor, note = budgets.ratchet(90.0, 120.0, reason="decision D-raise-budget")
    assert floor == pytest.approx(120.0) and "reason" in note


def test_every_declared_budget_has_a_knob_value():
    cfg = PerfMetricsConfig()
    for name in ("gate", "test_file", "stage:lease", "stage:merge"):
        assert budgets.budget_seconds(name, cfg) > 0


def test_an_unknown_budget_name_is_refused():
    with pytest.raises(KeyError):
        budgets.budget_seconds("nope", PerfMetricsConfig())
