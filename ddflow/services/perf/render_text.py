"""The perf report's text and markdown renderer (PM-report-text).

One deterministic renderer over the ``Report`` data layer: markdown tables, unicode
sparklines and bar rows, width-bounded for a terminal and a chat reply; a short headline
first (what got slower or faster, the top proposals), then the sections; a plain-dict form
for tools. This is the default answer to a perf report prompt over MCP, so no shell or
browser is needed (r-no-shell). A section the log cannot support is rendered ``n/a`` or
named ``not collected``, never as an empty table (r-unavailable).
"""

from __future__ import annotations

from ..metrics.render import dur, pct
from .report import Report

#: The eight block characters a sparkline uses, low to high. A bar row reuses them.
_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 20) -> str:
    """A unicode sparkline of ``values`` over at most ``width`` cells: a series longer than
    ``width`` is bucketed (each cell is its slice's max), a value that is ``None``
    (unavailable) is left out rather than plotted as a low, and nothing is the empty string
    (not a blank run, never a fabricated zero)."""
    xs = [v for v in values if v is not None]
    if not xs or width <= 0:
        return ""
    if len(xs) > width:
        step = len(xs) / width
        xs = [max(xs[int(i * step) : int((i + 1) * step)]) for i in range(width)]
    lo, hi = min(xs), max(xs)
    if hi <= lo:
        return _SPARK[0] * len(xs)
    span = hi - lo
    return "".join(_SPARK[round((v - lo) / span * (len(_SPARK) - 1))] for v in xs)


def bar(fraction: float | None, width: int = 16) -> str:
    """One bar row for a share in ``[0, 1]``; ``None`` (under the sample floor) is ``n/a``."""
    if fraction is None:
        return "n/a"
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else f"{text[: width - 1]}…"


def _headline(report: Report, width: int) -> list[str]:
    """The short headline: which gate is slowest (three at most), and what is not collected
    yet. One window has no time trend, so the trend sparkline is drawn from a real time
    series (the charts renderer's job when its source lands), not from gate names."""
    slow = sorted(
        (r for r in report.gates if r.get("p90_s") is not None),
        key=lambda r: r["p90_s"],
        reverse=True,
    )[:3]
    out = ["perf report"]
    if slow:
        out.append("  slower: " + ", ".join(f"{r['gate']} p90 {dur(r['p90_s'])}" for r in slow))
    proposals = report.not_collected.get("findings", "")
    if proposals:
        out.append(f"  proposals: not collected yet ({proposals})")
    return [_clip(line, width) for line in out]


def _gates(report: Report, width: int, max_rows: int) -> list[str]:
    """The per-gate table: time, first-run pass rate and a bar of the ever-failed share.
    An empty gate set is no table at all (never a header-only one), and a gate set narrower
    than the header is plain ``gate: ...`` rows, never a table with its pipes clipped off."""
    if not report.gates:
        return []
    rows = sorted(report.gates, key=lambda r: r["gate"])[:max_rows]
    header = "| gate | n | p50 | p90 | pass | failed |"
    if width > len(header):
        out = [header, "|---|---|---|---|---|---|"]
        for r in rows:
            out.append(
                "| "
                + " | ".join(
                    [
                        r["gate"],
                        str(r["n"]),
                        dur(r.get("p50_s")),
                        dur(r.get("p90_s")),
                        pct(r.get("first_run_pass_rate")),
                        f"{bar(r.get('ever_failed_share'), 2)} {pct(r.get('ever_failed_share'))}",
                    ]
                )
                + " |"
            )
    else:
        out = [
            f"{r['gate']}: n={r['n']} p50 {dur(r.get('p50_s'))} p90 {dur(r.get('p90_s'))}"
            f" pass {pct(r.get('first_run_pass_rate'))}"
            for r in rows
        ]
    if len(report.gates) > max_rows:
        out.append(f"… and {len(report.gates) - max_rows} more gates omitted (full rows in json)")
    return [_clip(line, width) for line in out]


def _comparison(report: Report, width: int) -> list[str]:
    tables: list[str] = []
    for name, rows in (
        ("model", report.comparisons.get("models", [])),
        ("reviewer", report.comparisons.get("reviewers", [])),
        ("agent", report.comparisons.get("agents", [])),
    ):
        if not rows:
            continue
        for r in rows:
            tables.append(
                f"{name}  {r['name']}  {bar(r.get('first_run_pass_rate'), 2)} {pct(r.get('first_run_pass_rate'))}"
            )
    return [_clip(line, width) for line in tables]


def render(report: Report, *, width: int = 76, max_rows: int = 20) -> str:
    """The markdown rendering: headline, cycle, waits, rework, gates, comparisons, then the
    not-collected sections named with the task that will provide them. Truncation names how
    much was cut and the json form for the rest."""
    width = max(width, 1)
    lines: list[str] = _headline(report, width)
    lines.append("")
    lines.append(
        _clip(
            f"cycle  n={report.cycle.get('n', 0)}  p50 {dur(report.cycle.get('p50_s'))}  p90 {dur(report.cycle.get('p90_s'))}",
            width,
        )
    )
    lines.append(
        _clip(
            f"waits  lease p50 {dur(report.waits.get('lease', {}).get('p50_s'))} "
            f" merge p50 {dur(report.waits.get('merge', {}).get('p50_s'))}",
            width,
        )
    )
    lines.append(_clip(f"rework  {len(report.rework)} items hit by a later bug", width))
    gates = _gates(report, width, max_rows)
    if gates:
        lines.append("")
        lines += gates
    comparison = _comparison(report, width)
    if comparison:
        lines.append("")
        lines += comparison
    if report.not_collected:
        lines.append("")
        lines.append("not collected:")
        lines += [
            _clip(f"  {section}: {task}", width)
            for section, task in sorted(report.not_collected.items())
        ]
    lines.append("")
    lines.append(
        _clip("cut: this text is bounded by max_rows; the json form carries every section", width)
    )
    return "\n".join(lines)


def to_json(report: Report) -> dict:
    """The report as plain data for tools: every section, with ``not_collected`` named (an
    uncomputed section is never an empty table)."""
    return {
        "gates": report.gates,
        "cycle": report.cycle,
        "waits": report.waits,
        "rework": report.rework,
        "comparisons": report.comparisons,
        "not_collected": report.not_collected,
    }


__all__ = ["bar", "render", "sparkline", "to_json"]
