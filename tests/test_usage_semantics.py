"""Regression tests for per-harness usage semantics (ORC-usage-semantics).

The invariant these pin: figures merge the way each harness DECLARES them --
cumulative merges by MAX (a running total), not SUM -- so two events from a cumulative
harness are never double-counted, and a harness that exposes nothing reads UNAVAILABLE,
never zero.
"""

from __future__ import annotations

import ddflow.services.metrics.usage_semantics as U


def test_claude_code_declares_cumulative():
    s = U.semantics_for("claude-code")
    assert s.mode == U.CUMULATIVE
    assert s.subagents == U.INCLUDED
    assert s.model_named_per_event is True


def test_codex_declares_cumulative_and_not_per_event_model():
    s = U.semantics_for("codex")
    assert s.mode == U.CUMULATIVE
    assert s.model_named_per_event is False


def test_an_undeclared_harness_reads_none():
    s = U.semantics_for("devin")
    assert s.mode == U.NONE
    assert not s.readable


def test_two_events_from_a_cumulative_harness_are_not_summed():
    # The falsifier: Codex reports running totals per thread; summing two of them
    # double-counts. Cumulative merges by MAX so the same figure never grows by the
    # overlap.
    assert U.merge_figures(U.CUMULATIVE, [10, 10]) == 10
    assert U.merge_figures(U.CUMULATIVE, [10, 19]) == 19
    assert U.merge_figures(U.CUMULATIVE, [10, 10]) != 20


def test_delta_sums_and_final_takes_last():
    assert U.merge_figures(U.DELTA, [10, 5]) == 15
    assert U.merge_figures(U.FINAL, [4, 5, 6]) == 6


def test_none_reads_none_never_zero():
    assert U.merge_figures(U.NONE, [3]) is None
    assert U.resolve_figure("devin", [5, 5]) is None


def test_resolve_figure_for_a_cumulative_harness():
    assert U.resolve_figure("claude-code", [7, 7, 9]) == 9


def test_the_shipped_table_loads_and_names_the_declared_families():
    table = U.load()
    assert {"claude-code", "codex", "opencode"} <= set(table)
    for family in table:
        assert table[family]["mode"] in U.MODES, family
