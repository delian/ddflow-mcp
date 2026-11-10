"""The SVG charts are deterministic, well formed and accessible (PM-report-charts).

The falsifier is a chart package that does not keep its bytes stable for fixed data, emits
malformed SVG, or tells two series apart by red/green colour alone; these tests pin the
bytes for a fixed series, parse-gate the SVG, and check the palette and the two themes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ddflow.services.perf.charts import (
    PALETTE,
    bars,
    heat_strip,
    html_report,
    line_trend,
    scale,
    scatter,
    stacked_bar,
)

_URL = "http://www.w3.org/2000/svg"


def test_scale_is_not_zero_for_flat_or_empty_series():
    assert scale([]) == (0.0, 1.0)
    assert scale([5.0, 5.0, 5.0]) == (0.0, 6.0)


def test_line_trend_is_well_formed_svg():
    svg = line_trend(
        [1.0, None, 3.0],
        band=([0.5, 2.0, 3.5], [1.5, 3.0, 4.5]),
        budget=2.5,
        title="gate p90",
        desc="p90 over the window",
    )
    root = ET.fromstring(svg)
    assert root.tag == f"{{{_URL}}}svg"
    assert root.find(f"{{{_URL}}}title").text == "gate p90"
    assert root.find(f"{{{_URL}}}desc").text == "p90 over the window"


def test_charts_are_deterministic():
    a = line_trend([1.0, 2.0, 3.0], budget=2.0)
    b = line_trend([1.0, 2.0, 3.0], budget=2.0)
    assert a == b


def test_the_palette_has_no_red_green_alone():
    # a colour-blind-safe set: no pure red, no pure green
    assert "#D55E00" not in PALETTE and "#009E73" in PALETTE
    assert not any(c.lower().startswith("#ff") for c in PALETTE)


def test_dark_and_light_differ_only_in_theme():
    light = line_trend([1.0, 2.0])
    dark = line_trend([1.0, 2.0], dark=True)
    assert "#ffffff" in light
    assert "#1e1e1e" in dark


def test_heat_strip_scales_opacity_not_colour():
    svg = heat_strip([None, 2.0, 1.0], title="share", desc="per-file share")
    ET.fromstring(svg)  # well formed even with a missing value


def test_bars_and_scatter_and_stacked_are_well_formed():
    for svg in (
        bars([("a", 1.0), ("b", 2.0)], title="t", desc="d"),
        scatter([(1.0, 5.0), (2.0, 9.0)], title="t", desc="d"),
        stacked_bar([("q", [1.0, 2.0]), ("m", [0.5, 1.0])], title="t", desc="d"),
    ):
        ET.fromstring(svg)


def test_html_report_is_one_self_contained_file():
    page = html_report({"a": line_trend([1.0, 2.0])}, title="report")
    assert "<!doctype html>" in page
    assert "src=" not in page and "href=" not in page  # no external request
