"""Time budgets and the shrink-only ratchet (PM-budgets): breach, floor, severity, next call."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.services.perf import budgets


def test_a_series_under_budget_is_a_clear_not_a_breach():
    cfg = PerfMetricsConfig(budget_gate_p90_s=180.0, budget_breach_min_samples=3)
    c = budgets.check("gate", [10.0] * 12, cfg=cfg)
    assert c.status == "ok"
    assert c.enough and c.breach is False and c.p90_s is not None


def test_a_series_over_budget_is_a_breach_naming_the_budget():
    cfg = PerfMetricsConfig(budget_gate_p90_s=60.0, budget_breach_min_samples=3)
    c = budgets.check("gate", [70.0] * 12, cfg=cfg)
    assert c.status == "breach" and c.budget == "gate" and c.kind == "gate"
    assert c.budget_s == pytest.approx(60.0) and c.n == 12
    assert c.p90_s >= 60.0


def test_below_the_sample_floor_is_unavailable_not_a_false_clear():
    # two samples over budget are below the floor: 'insufficient', distinctly, never a clear
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_breach_min_samples=5)
    c = budgets.check("test_file", [99.0, 99.0], cfg=cfg)
    assert c.status == "insufficient"
    assert c.enough is False and c.p90_s is None and c.breach is False


def test_under_floor_and_under_budget_are_distinct():
    # 'ok' and 'insufficient' are NOT the same status (a caller reads status, not the void)
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_breach_min_samples=5)
    short = budgets.check("test_file", [99.0], cfg=cfg)  # over budget, but no data
    clear = budgets.check("test_file", [1.0, 1.0, 1.0, 1.0, 1.0], cfg=cfg)  # ran, under budget
    assert short.status == "insufficient" and clear.status == "ok"


def test_checks_surfaces_an_insufficient_series_not_drops_it():
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_breach_min_samples=5)
    out = budgets.checks({"test_file": [99.0, 99.0]}, cfg=cfg)
    assert [c.status for c in out] == ["insufficient"]  # present, marked unavailable, never 'ok'


def test_default_severity_is_warn_a_finding_not_a_refusal():
    cfg = PerfMetricsConfig(budget_gate_p90_s=60.0)
    c = budgets.check("gate", [99.0] * 10, cfg=cfg)
    assert c.breach and c.severity == "warn"
    assert c.blocks is False


def test_severity_block_is_a_refusal_naming_the_next_call():
    cfg = PerfMetricsConfig(budget_test_file_s=30.0, budget_severity="block")
    c = budgets.check("test_file", [99.0] * 10, cfg=cfg)
    assert c.breach and c.blocks is True
    assert "ddflow tests --full" in c.next_call()  # CLI form
    assert "MCP:" in c.next_call()  # MCP form named too (r-tell-dont-discover)


def test_a_stage_budget_breach_guides_raise_with_a_reason_only():
    cfg = PerfMetricsConfig(budget_stage_lease_wait_s=30.0, budget_severity="block")
    c = budgets.check("stage:lease", [99.0] * 10, cfg=cfg)
    assert c.breach and c.kind == "stage"
    assert "ddflow decision add" in c.next_call()  # raising needs a recorded reason


def test_breaches_returns_only_the_breaches():
    cfg = PerfMetricsConfig(
        budget_gate_p90_s=60.0, budget_test_file_s=30.0, budget_breach_min_samples=3
    )
    out = budgets.breaches({"gate": [10.0] * 12, "test_file": [99.0] * 12}, cfg=cfg)
    assert [c.budget for c in out] == ["test_file"]


def test_the_ratchet_only_tightens():
    floor, note = budgets.ratchet(120.0, 90.0)
    assert floor == pytest.approx(90.0) and "tighten" in note


def test_raising_the_floor_without_a_reason_is_refused():
    floor, note = budgets.ratchet(90.0, 120.0)
    assert floor == pytest.approx(90.0)  # unchanged, never silently raised
    assert "refused" in note


def test_raising_the_floor_on_a_blank_reason_is_refused():
    floor, note = budgets.ratchet(90.0, 120.0, reason="   ")
    assert floor == pytest.approx(90.0) and "refused" in note


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
