"""render_text: width-bounded markdown, unicode sparklines and bar rows, json for tools.

The falsifier is a renderer that fabricates a zero for a missing section or loses its
width bound; these tests pin the empty-data and the width-limit behaviour, plus the
sparkline and bar utilities on degenerate input.
"""

from __future__ import annotations

from ddflow.services.perf.render_text import bar, render, sparkline, to_json
from ddflow.services.perf.report import Report


def _sample() -> Report:
    return Report(
        gates=[
            {
                "gate": "ci",
                "n": 20,
                "p50_s": 30.0,
                "p90_s": 90.0,
                "max_s": 200.0,
                "first_run_pass_rate": 0.6,
                "ever_failed_share": 0.3,
            },
            {
                "gate": "critic",
                "n": 20,
                "p50_s": 60.0,
                "p90_s": 150.0,
                "max_s": 400.0,
                "first_run_pass_rate": 0.4,
                "ever_failed_share": 0.5,
            },
        ],
        cycle={"n": 20, "p50_s": 3600.0, "p90_s": 7200.0, "max_s": 10000.0, "items": 20},
        waits={
            "lease": {"n": 20, "p50_s": 100.0, "p90_s": 300.0, "max_s": 500.0},
            "merge": {"n": 20, "p50_s": 50.0, "p90_s": 100.0, "max_s": 200.0},
        },
        rework=[{"item": "T1", "completed_at": 100.0, "bug_at": 200.0}],
        comparisons={
            "models": [{"name": "m-a", "n": 20, "env": "", "first_run_pass_rate": 0.8}],
            "reviewers": [],
            "agents": [{"name": "a1", "n": 20, "env": "", "first_run_pass_rate": 0.7}],
        },
        not_collected={"test_suite_size": "PM-test-durations", "findings": "PM-detect"},
    )


def test_an_empty_report_renders_named_not_collected_not_blank():
    text = render(Report())
    assert text.strip()  # never a blank answer
    # an empty report has no gates, so the deliveries carry no fabricated table
    assert "cycle" in text


def test_render_names_not_collected_with_their_task():
    text = render(_sample())
    assert "not collected" in text
    assert "PM-detect" in text


def test_render_is_width_bounded():
    for width in (20, 40, 76):
        text = render(_sample(), width=width)
        for line in text.splitlines():
            assert len(line) <= width


def test_the_headline_names_the_slowest_gate_first():
    text = render(_sample())
    assert "slowest:" in text
    assert "critic" in text  # p90 150s is the slowest of the two


def test_sparkline_empty_and_constant_are_not_a_blank_run():
    assert sparkline([]) == ""
    assert sparkline([5.0, 5.0, 5.0], 4) == "▁▁▁"


def test_sparkline_buckets_a_long_series_into_width_cells():
    # five values into two cells: the max of each slice, never five glyphs
    assert len(sparkline([0.0, 1.0, 2.0, 3.0, 4.0], 2)) == 2


def test_sparkline_tracks_the_series_shape():
    line = sparkline([0.0, 5.0, 10.0], 3)
    assert line[0] == "▁" and line[-1] == "█" and len(line) == 3


def test_bar_none_is_na_and_one_is_full():
    assert bar(None) == "n/a"
    assert bar(1.0, 4) == "████"
    assert bar(0.0, 4) == "░░░░"


def test_json_is_the_plain_sections():
    data = to_json(_sample())
    assert set(data) == {"gates", "cycle", "waits", "rework", "comparisons", "not_collected"}
    assert data["gates"][0]["gate"] == "ci"
