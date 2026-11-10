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
    """A unicode sparkline of ``values`` scaled to ``width`` cells; the empty series is an
    empty string (not a blank run, and never a fabricated zero)."""
    xs = [v for v in values if v is not None]
    if not xs:
        return ""
    lo, hi = min(xs), max(xs)
    if hi == lo:
        return _SPARK[0] * width
    step = hi - lo
    return "".join(_SPARK[int((v - lo) / step * (len(_SPARK) - 1))] for v in xs)


def bar(fraction: float | None, width: int = 16) -> str:
    """One bar row for a share in ``[0, 1]``; ``None`` (under the sample floor) is ``n/a``."""
    if fraction is None:
        return "n/a"
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def _headline(report: Report, width: int) -> list[str]:
    """The short headline: which gate is slowest (three at most), and what is not collected
    yet. Kept to ``width`` columns; longer rows are truncated with the count."""
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


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else f"{text[: width - 1]}…"


def _gates(report: Report, width: int) -> list[str]:
    """The per-gate table: time, first-run pass rate and a bar of the ever-failed share."""
    header = "| gate | n | p50 | p90 | pass | failed |"
    sep = "|---|---|---|---|---|---|"
    rows = [header, sep]
    for r in sorted(report.gates, key=lambda r: r["gate"]):
        rows.append(
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
    return [_clip(line, width) for line in rows]


def _comparison(report: Report, width: int) -> list[str]:
    tables: list[str] = []
    for name, rows in (
        ("model", report.comparisons.get("models", [])),
        ("reviewer", report.comparisons.get("reviewers", [])),
        ("agent", report.comparisons.get("agents", [])),
    ):
        tables += [
            f"{name}        pass",
            *[
                f"  {r['name']}  {bar(r.get('first_run_pass_rate'), 2)} {pct(r.get('first_run_pass_rate'))}"
                for r in rows
            ],
        ]
    return [_clip(line, width) for line in tables]


def render(report: Report, *, width: int = 76) -> str:
    """The markdown rendering: headline, gates, cycle, waits, rework, comparisons, then the
    not-collected sections named with the task that will provide them."""
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
    lines.append("")
    lines += _gates(report, width)
    lines.append("")
    lines += _comparison(report, width)
    if report.not_collected:
        lines.append("")
        lines.append("not collected:")
        lines += [
            _clip(f"  {section}: {task}", width)
            for section, task in sorted(report.not_collected.items())
        ]
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
