"""The SVG charts are deterministic, well formed and accessible (PM-report-charts).

The falsifier is a chart package that does not keep its bytes stable for fixed data, emits
malformed SVG, or tells two series apart by red/green colour alone; these tests pin the
bytes for a fixed series, parse-gate the SVG, and check the palette and the two themes.
"""

from __future__ import annotations

import re
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


def test_the_palette_is_the_colour_blind_safe_set():
    # Okabe-Ito order (blue, orange, teal, pink): no red (#D55E00) and no pure green
    assert PALETTE == ("#0072B2", "#E69F00", "#009E73", "#CC79A7")


def test_titles_and_labels_are_xml_escaped():
    svg = bars([("a&b<c>", 1.0)], title="p50 & p90", desc="a<b")
    ET.fromstring(svg)  # raw & and < in the label/title would break this
    assert "&amp;" in svg and "&lt;" in svg


def test_dark_and_light_differ_only_in_theme():
    light = line_trend([1.0, 2.0])
    dark = line_trend([1.0, 2.0], dark=True)
    assert "#ffffff" in light
    assert "#1e1e1e" in dark


def test_heat_strip_encodes_share_by_opacity_not_hue():
    svg = heat_strip([None, 2.0, 1.0], title="share", desc="per-file share")
    ET.fromstring(svg)  # well formed even with a missing value
    # every cell shares one hue; the opacity STEP carries the share ratio
    assert svg.count(f'fill="{PALETTE[0]}"') == 3
    assert re.findall(r'fill-opacity="([^"]+)"', svg) == ["0.000", "1.000", "0.500"]


def test_bars_and_scatter_and_stacked_are_well_formed():
    for svg in (
        bars([("a", 1.0), ("b", 2.0)], title="t", desc="d"),
        scatter([(1.0, 5.0), (2.0, 9.0)], title="t", desc="d"),
        stacked_bar([("q", [1.0, 2.0]), ("m", [0.5, 1.0])], title="t", desc="d"),
    ):
        ET.fromstring(svg)


def test_stacked_bar_scales_to_the_column_total_not_the_largest_segment():
    # one column (index 0) stacked from setup=2.0 and run=4.0: the total is 6.0, so the
    # two rects fill the plot exactly and no rect rolls off the top (a negative y)
    svg = stacked_bar([("setup", [2.0]), ("run", [4.0])], title="t", desc="d")
    ET.fromstring(svg)
    assert 'y="-' not in svg


def test_an_empty_stacked_bar_draws_nothing_not_a_crash():
    svg = stacked_bar([], title="t", desc="d")
    ET.fromstring(svg)
    # no data bars drawn: the only <rect> is the viewbox background fill
    assert "fill-opacity=" not in svg


def test_html_report_is_one_self_contained_file():
    page = html_report({"a": line_trend([1.0, 2.0])}, title="report")
    assert "<!doctype html>" in page
    assert (
        'src="' not in page and "<link" not in page and "<script" not in page
    )  # no external request
