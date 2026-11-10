"""The `perf report` action's API half (PM-report-surfaces).

One entry point behind the CLI (`ddflow perf report`), the MCP action
(`ddflow_view action=perf`) and the plain prompt trigger: a user who asks for a
performance report gets the rendered report back as text, and the SVG/HTML forms are
written under ``.ddflow/local/perf/reports/`` with the path and the exact next calls named
(r-tell-dont-discover, r-no-shell). The data layer (`services/perf/report.build`) is built
once and the renderer is chosen by ``format`` -- text|md|json|svg|html. A figure the log
cannot support is ``n/a`` or named ``not collected``, never a zero, and a bad format or
window is refused, never passed (r-unavailable).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..core import outcome as O
from ..services.metrics import service as MS
from ..services.metrics.facts import collect
from ..services.metrics.providers import Context
from ..services.perf import charts as CH
from ..services.perf import render_text as RT
from ..services.perf.report import build
from ._base import _load

#: The report formats one declaration serves. ``text`` and ``md`` are the same markdown
#: answer (the renderer is the text-and-markdown renderer); ``json`` is the plain-dict
#: report serialised; ``svg`` and ``html`` are the chart set, written to a file.
FORMATS = ("text", "md", "json", "svg", "html")

#: Where svg/html reports are written when the caller gives no ``out``: under the local
#: perf area, alongside the raw samples (never the committed log).
OUT_DIR = ".ddflow/local/perf/reports"


def _phase_filter(filters: str) -> str:
    """The one filter the report's Context reads: ``phase=<id>``. Anything else is
    refused, never silently ignored (r-tell-dont-discover)."""
    phase = ""
    for raw in (filters or "").split(","):
        part = raw.strip()
        if not part:
            continue
        if part.startswith("phase="):
            phase = part[len("phase=") :].strip()
        else:
            raise ValueError(f"unknown filter {part!r}: only phase=<id> is a report filter")
    return phase


def _charts(report) -> dict[str, str]:
    """The chart set the svg and html formats render: a bar chart of per-gate p90 wall
    time and a bar chart of first-run pass rate per model. A row without a figure draws
    no bar (the chart package never plots a missing value as a zero)."""
    out: dict[str, str] = {}
    gate_rows = [(g["gate"], g["p90_s"]) for g in report.gates if g.get("p90_s") is not None]
    if gate_rows:
        out["gate_p90"] = CH.bars(
            gate_rows,
            title="gate p90",
            desc="p90 wall time per gate (seconds)",
        )
    model_rows = [
        (r["name"], 100.0 * r["first_run_pass_rate"])
        for r in report.comparisons.get("models", [])
        if r.get("first_run_pass_rate") is not None
    ]
    if model_rows:
        out["model_pass"] = CH.bars(
            model_rows,
            title="first-run pass rate per model",
            desc="percent of RAN gate verdicts passed on the first run",
        )
    return out


def _next_calls(fmt: str, out: str, win: str) -> list[str]:
    """The exact next calls in CLI and MCP form (r-tell-dont-discover)."""
    cli = f"ddflow perf report --format {fmt} --window {win} --out {out}"
    mcp = f"ddflow_view action=perf format={fmt} window={win} out={out}"
    return [cli, mcp]


def _footer(fmt: str, out: str, win: str) -> str:
    return "next:\n  " + "\n  ".join(_next_calls(fmt, out, win))


def _target(repo: Path, fmt: str, out: str, now: float) -> Path:
    """The file the svg/html report is written to: ``out`` when given, else the dated
    default under ``.ddflow/local/perf/reports/``."""
    if out:
        p = Path(out)
        return p if p.is_absolute() else repo / p
    return repo / OUT_DIR / f"report-{int(now)}.{fmt}"


def run_perf_report(
    repo: Path,
    *,
    window: str = "",
    filters: str = "",
    format: str = "text",
    out: str = "",
    now: float | None = None,
    agent: str = "",
) -> O.Outcome:
    """The performance report over the event log for a ``window``.

    ``window`` is a duration back (7d, 12h) or a date (YYYY-MM-DD), default
    ``[perf_metrics].report_default_window``; ``filters`` narrows to ``phase=<id>``;
    ``format`` is one of text|md|json|svg|html; ``out`` is where svg/html are written
    (default ``.ddflow/local/perf/reports/``). Deterministic for the same log and config."""
    log, cfg, st = _load(repo, agent)
    at = time.time() if now is None else now
    fmt = (format or "text").strip().lower()
    if fmt not in FORMATS:
        return O.refused(
            "perf_report", f"format {format!r}: one of {', '.join(FORMATS)}"
        )
    win = (window or "").strip() or cfg.perf_metrics.report_default_window
    try:
        since = MS.parse_since(win, at)
    except ValueError as exc:
        return O.refused("perf_report", str(exc))
    try:
        phase = _phase_filter(filters)
    except ValueError as exc:
        return O.refused("perf_report", str(exc))
    if phase and phase not in st.items:
        return O.refused("perf_report", f"no phase {phase!r}")
    facts = collect(log.read_all(), st, at, cfg)
    ctx = Context(facts, cfg, at, since, phase)
    report = build(ctx)
    width = int(cfg.perf_metrics.report_width)
    max_rows = int(cfg.perf_metrics.report_max_rows)

    if fmt in ("text", "md"):
        body = RT.render(report, width=width, max_rows=max_rows) + "\n" + _footer(fmt, "", win)
    elif fmt == "json":
        body = json.dumps(RT.to_json(report), indent=2)
    else:
        charts = _charts(report)
        if fmt == "svg":
            doc = "\n\n".join(charts.values())
        else:
            doc = CH.html_report(charts, title="perf report")
        path = _target(repo, fmt, out, at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc, "utf-8")
        body = f"path: {path}\n{doc}\n{_footer(fmt, str(path), win)}"
    return O.ok("perf_report", text=body, format=fmt, window=win, out=out, report=RT.to_json(report))


__all__ = ["FORMATS", "OUT_DIR", "run_perf_report"]
