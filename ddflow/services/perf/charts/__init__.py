"""The perf report's SVG charts and self-contained HTML page (PM-report-charts).

A small stdlib SVG package (no third-party plotting): every chart is a pure function of
plain data -> a deterministic SVG string, accessible (``<title>``/``<desc>``, a
colour-blind-safe palette, dash patterns as well as colour) and themeable (light/dark).
``html_report`` inlines the SVGs and the tables in one file with no external requests.
Deterministic: every number is fixed precision and text is XML-escaped, so fixed data
always draws the same well-formed bytes. A series that cannot support a figure draws no
line or point for the missing value, never a zero point.
"""

from __future__ import annotations

from collections.abc import Iterable

#: A colour-blind-safe palette (blue, orange, teal, pink: no red/green alone). A bar's
#: dash/dashless stroke and its axis label tell two series apart, not hue alone.
PALETTE = ("#0072B2", "#E69F00", "#009E73", "#CC79A7")


def _fmt(value: float) -> str:
    """A number at fixed precision: the same input always becomes the same bytes."""
    return f"{value:.3f}"


def _esc(text: str) -> str:
    """XML-escape ``& < >`` so a title, description or label never breaks the SVG/HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def scale(values: Iterable[float | None], *, positive: bool = True) -> tuple[float, float]:
    """The ``(lo, hi)`` of a series; a ``None`` is left out, and a flat series still has a
    non-zero span. ``positive`` floors lo at 0.0 (a count or duration is never negative)."""
    xs = [v for v in values if v is not None]
    lo, hi = (min(xs), max(xs)) if xs else (0.0, 1.0)
    if hi == lo:
        hi = lo + 1.0
    return (0.0 if positive and lo >= 0 else lo, hi)


def viewbox(width: int, height: int, *, dark: bool = False) -> str:
    """The shared SVG wrapper: an accessible, themed canvas every chart draws on."""
    bg = "#1e1e1e" if dark else "#ffffff"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img"'
        f'><rect width="{width}" height="{height}" fill="{bg}"/>'
    )


def _head(title: str, desc: str) -> str:
    return f"<title>{_esc(title)}</title><desc>{_esc(desc)}</desc>"


def line_trend(
    series: list[float | None],
    *,
    band: tuple[list[float | None], list[float | None]] | None = None,
    budget: float | None = None,
    width: int = 600,
    height: int = 200,
    title: str = "",
    desc: str = "",
    dark: bool = False,
) -> str:
    """A line trend over ``series`` (the p50), an optional ``(lower, upper)`` p50/p90 band,
    and a budget line. The y-scale spans the series, the band and the budget, so the band
    and the budget line are always on the canvas. A ``None`` draws nothing for that column."""
    pad = 40.0
    bounds = list(series)
    if band is not None:
        bounds += list(band[0]) + list(band[1])
    if budget is not None:
        bounds.append(budget)
    lo, hi = scale(bounds)
    fg = "#d6d6d6" if dark else "#1a1a1a"

    def x(i: int) -> float:
        n = len(series)
        return pad + (width - 2 * pad) * (i / (n - 1) if n > 1 else 0.5)

    def y(v: float) -> float:
        return height - pad - (height - 2 * pad) * ((v - lo) / (hi - lo))

    def poly(xs: list[float | None], *, colour: str, dash: bool = False) -> str:
        # a gap is a break: one stroke per contiguous run, and a lone sample is a marker
        # (SVG does not stroke a one-point polyline), so a missing sample draws nothing
        # and a single present sample is still visible
        runs: list[list[tuple[float, float]]] = []
        cur: list[tuple[float, float]] = []
        for i, v in enumerate(xs):
            if v is None:
                if cur:
                    runs.append(cur)
                    cur = []
                continue
            cur.append((x(i), y(v)))
        if cur:
            runs.append(cur)
        dash_attr = ' stroke-dasharray="4 4"' if dash else ""
        out = []
        for seg in runs:
            if len(seg) == 1:
                px, py = seg[0]
                out.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="3" fill="{colour}"/>')
            else:
                d = " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in seg)
                out.append(
                    f'<polyline fill="none" stroke="{colour}" stroke-width="2"{dash_attr} points="{d}"/>'
                )
        return "".join(out)

    body = [poly(series, colour=PALETTE[0])]
    if band is not None:
        lower, upper = band
        body.append(poly(lower, colour=PALETTE[1], dash=True))
        body.append(poly(upper, colour=PALETTE[1], dash=True))
    if budget is not None:
        by = _fmt(y(budget))
        body.append(
            f'<line x1="{_fmt(pad)}" y1="{by}" x2="{_fmt(width - pad)}" y2="{by}" '
            f'stroke="{fg}" stroke-dasharray="2 3"/>'
        )
    return viewbox(width, height, dark=dark) + _head(title, desc) + "".join(body) + "</svg>"


def bars(
    values: list[tuple[str, float]],
    *,
    width: int = 600,
    height: int = 200,
    title: str = "",
    desc: str = "",
    dark: bool = False,
) -> str:
    """Bars of labelled values; a bar is coloured by index and its label is drawn under
    it, so a row is never told apart by hue alone (the label is the axis text)."""
    lo, hi = scale([v for _k, v in values])
    pad = 40.0
    fg = "#d6d6d6" if dark else "#1a1a1a"
    n = len(values)
    span = width - 2 * pad
    step = span / n if n else 1.0
    body = []
    for i, (label, v) in enumerate(values):
        h = (height - 2 * pad) * ((v - lo) / (hi - lo))
        x0 = pad + step * i
        body.append(
            f'<rect x="{_fmt(x0)}" y="{_fmt(height - pad - h)}" width="{_fmt(step * 0.8)}" '
            f'height="{_fmt(h)}" fill="{PALETTE[i % len(PALETTE)]}"/>'
        )
        body.append(
            f'<text x="{_fmt(x0)}" y="{_fmt(height)}" fill="{fg}" font-size="10">{_esc(label)}</text>'
        )
    return viewbox(width, height, dark=dark) + _head(title, desc) + "".join(body) + "</svg>"


def heat_strip(
    shares: list[float | None],
    *,
    width: int = 600,
    height: int = 24,
    title: str = "",
    desc: str = "",
    dark: bool = False,
) -> str:
    """One heat strip of per-file shares: each cell's opacity encodes its share of the
    total; a missing share (``None``) draws a gap (no cell), never the same mark as a real
    zero share, so an unavailable probe is not recorded as zero."""
    vals = [v for v in shares if v is not None]
    hi = max(vals) if vals else 1.0
    cell = width / max(len(shares), 1)
    body = "".join(
        f'<rect x="{_fmt(i * cell)}" y="0" width="{_fmt(cell)}" height="{height}" '
        f'fill="{PALETTE[0]}" fill-opacity="{_fmt((v / hi) if hi else 0.0)}"/>'
        for i, v in enumerate(shares)
        if v is not None
    )
    return viewbox(width, height, dark=dark) + _head(title, desc) + body + "</svg>"


def scatter(
    points: list[tuple[float, float]],
    *,
    width: int = 600,
    height: int = 200,
    title: str = "",
    desc: str = "",
    dark: bool = False,
) -> str:
    """A scatter of ``(x, y)`` points (e.g. test duration against file size)."""
    pad = 40.0
    xlo, xhi = scale([p[0] for p in points])
    ylo, yhi = scale([p[1] for p in points])
    body = []
    for x, y in points:
        px = pad + (width - 2 * pad) * (x - xlo) / (xhi - xlo)
        py = height - pad - (height - 2 * pad) * (y - ylo) / (yhi - ylo)
        body.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="3" fill="{PALETTE[0]}"/>')
    return viewbox(width, height, dark=dark) + _head(title, desc) + "".join(body) + "</svg>"


def stacked_bar(
    columns: list[list[float]],
    *,
    legend: list[str] | None = None,
    width: int = 600,
    height: int = 200,
    title: str = "",
    desc: str = "",
    dark: bool = False,
) -> str:
    """A stacked bar of ``columns``: column ``c`` stacks ``columns[c]`` (its segment values,
    bottom-up) against the column TOTAL (the sum, never the largest segment), each segment
    in a distinct palette step whose label is ``legend[s]`` (named in the ``<desc>``). An
    empty column list draws an empty chart, never a crash."""
    pad = 40.0
    legend = legend or []
    if not columns:
        return viewbox(width, height, dark=dark) + _head(title, desc) + "</svg>"
    totals = [sum(col) for col in columns]
    lo, hi = scale([0.0, max(totals)])
    span = width - 2 * pad
    step = span / len(columns)
    labels = ", ".join(legend)
    full_desc = f"{desc} segments: {labels}"
    body: list[str] = []
    for c, col in enumerate(columns):
        base = height - pad
        for s, v in enumerate(col):
            h = (height - 2 * pad) * ((v - lo) / (hi - lo))
            body.append(
                f'<rect x="{_fmt(pad + step * c)}" y="{_fmt(base - h)}" width="{_fmt(step * 0.8)}" '
                f'height="{_fmt(h)}" fill="{PALETTE[s % len(PALETTE)]}" '
                f'fill-opacity="{_fmt(min(1.0, 0.5 + 0.12 * s))}"/>'
            )
            base -= h
    return viewbox(width, height, dark=dark) + _head(title, full_desc) + "".join(body) + "</svg>"


def html_report(charts: dict[str, str], *, title: str, dark: bool = False) -> str:
    """A self-contained HTML page that inlines the SVGs (one file, no external request).
    ``title`` and each chart's own ``<title>``/``<desc>`` are escaped like the SVG text."""
    bg = "#1e1e1e" if dark else "#ffffff"
    fg = "#e0e0e0" if dark else "#1a1a1a"
    body = "".join(f"<div>{charts[k]}</div>" for k in sorted(charts))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title></head>"
        f'<body style="background:{bg};color:{fg};font-family:sans-serif">{body}</body></html>'
    )


__all__ = [
    "PALETTE",
    "bars",
    "heat_strip",
    "html_report",
    "line_trend",
    "scale",
    "scatter",
    "stacked_bar",
    "viewbox",
]
