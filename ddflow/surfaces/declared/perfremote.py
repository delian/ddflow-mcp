"""The `perf` command, declared once: the host fingerprint and the measuring wrapper.

`perf env` prints the machine-class fingerprint (never a hostname or path); `perf run`
returns the exact wrapped command an agent runs where ddflow cannot (`ddflow perf run
--cmd CMD`); `perf ingest` accepts one reported sample line at the reported trust level. The
parser half is registered by convention and the MCP half is `ddflow_perf` (D-unify 4).
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _call(repo, a, agent):
    action = a.get("action") or "env"
    if action == "env":
        return _api().run_perf_env(repo, agent=agent)
    if action == "run":
        cmd = a.get("cmd") or ""
        if isinstance(cmd, list):
            import shlex

            cmd = " ".join(shlex.quote(part) for part in cmd)
        return _api().run_perf_run(repo, cmd=cmd, agent=agent)
    if action == "report":
        return _api().run_perf_report(
            repo,
            window=a.get("window") or "",
            filters=a.get("filters") or "",
            format=a.get("format") or "text",
            out=a.get("out") or "",
            agent=agent,
        )
    return _api().run_perf_ingest(repo, line=a.get("line") or "", agent=agent)


def _text(out, a) -> str:
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("perf",),
        summary="perf of the host (fingerprint, measuring wrapper) and the perf report (window, filters, format)",
        tool="ddflow_perf",
        family="ddflow_view",
        action="perf",
        tier="all",
        help_group="Doing the work",
        description=(
            "Perf data about where ddflow runs. action=env: this machine class's fingerprint "
            "(a stable key over the class of machine, never a hostname, user or path, plus its "
            "run-time condition, facts and container flag). action=run: the exact wrapped "
            "command an agent runs where ddflow cannot run it itself -- `ddflow perf run --cmd CMD` "
            "-- whose wrapper prints one JSON line of wall, cpu, rss and io. action=ingest + line: "
            "accept one such reported sample line (an agent-reported timing is kept at the "
            "agent-reported trust level and never counted as gate evidence of a pass). "
            "action=report + window/filters/format/out: the performance report over the event "
            "log in one of text|md|json|svg|html (the SVG and HTML are written under "
            ".ddflow/local/perf/reports/ with the path and the exact next calls named). Malformed "
            "input is refused, never recorded as zero."
        ),
        call=_call,
        payload=("text",),
        render=_text,
        wire_shape=(["perf", "report", "--format", "json"], {"action": "report", "format": "json"}),
        params=(
            Param(
                "action",
                help="env (the fingerprint), run (measure a command), ingest (accept a sample line) or report (the performance report).",
                cli_help="env (default), run, ingest or report",
                positional=True,
                nargs="?",
                choices=("env", "run", "ingest", "report"),
            ),
            Param(
                "cmd",
                help=(
                    "with action=run: the command to measure. CLI: `ddflow perf run --cmd 'pytest -q'`."
                ),
                cli_help="with run: the command to measure",
            ),
            Param(
                "line",
                help="with action=ingest: the wrapper's one JSON line to accept.",
                cli_help="with ingest: the sample line",
            ),
            Param(
                "window",
                help="with action=report: the time window, a duration back (7d, 12h) or a date YYYY-MM-DD.",
                cli_help="with report: a duration back (7d, 12h) or a date YYYY-MM-DD",
                default="",
            ),
            Param(
                "filters",
                help="with action=report: comma-separated report filters; only phase=<id>.",
                cli_help="with report: only phase=<id>",
                default="",
            ),
            Param(
                "format",
                help="with action=report: text (default), md, json, svg or html.",
                cli_help="with report: text (default), md, json, svg or html",
                choices=("text", "md", "json", "svg", "html"),
                default="text",
            ),
            Param(
                "out",
                help="with action=report and format svg/html: the file to write (default .ddflow/local/perf/reports/).",
                cli_help="with report svg/html: the file path",
                default="",
            ),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
