"""The `metrics` command, declared once: the summary, forecast, retro, skips, lanes, stages, locks and swarm views.

`surfaces/parsers/metrics.py` registers the command-line half and `surfaces/tools/__init__.py`
takes the MCP entry (D-unify 4). Read-only: it appends no event and starts no process.
"""

from __future__ import annotations

from ...core.metrics import VIEWS
from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _text(out, a) -> str:
    """The human form is built by the service (`text`), shared by both surfaces."""
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("metrics",),
        summary="metrics from the log: cycle time, review rounds, waits; `forecast`, `retro <id>`, `skips`",
        tool="ddflow_metrics",
        description=(
            "Metrics derived from the event log, read-only. view=summary (default): cycle time, "
            "time per gate, review rounds per item, gate failure/skip/unavailable rates (group_by "
            "gate, reviewer, agent, kind or phase), queue and glob-conflict wait, rework. "
            "view=forecast: the critical-path ETA of a phase as a P50..P90 estimate. "
            "view=retro + item: that item's timeline and its biggest gap (why it was slow). "
            "view=skips: the gate-skip audit. view=lanes: lane utilisation on an hourly grid. "
            "view=stages: the cycle-time split (queue wait, lease wait, implement, per-gate run and "
            "host-lock wait, review wait, merge wait, claim to merge, rework rounds, gate re-runs), "
            "group_by phase, kind, size (changed lines) or agent; stages whose events ddflow does "
            "not record yet are listed as not measured, never 0. "
            "view=locks: what the leases cost (hold split: claim to first gate, gates, reviews, last "
            "review to release, after merge; claim waits and false serialization; declared versus "
            "written files; merge-tree conflict and red-main rates) and whether the recorded "
            "falsifiers tripped, with the call that switches back to strict locking. "
            "view=swarm: the baseline a lane fleet must beat: merged items per hour, rework rate, "
            "agent-hours per item, critical-path slack. "
            "view=tiny: one-line versions of the summary figures. "
            "view=paths: each item's workflow path (process vs record steps) and the burst union of "
            "concurrent process time. view=variants: the directly-follows gate graph, top variants, "
            "burst overlap, superseded passes and out-of-order runs. "
            "view=slo: the service-level indicators ddflow tracks for itself. "
            "A figure under [metrics].min_samples is n/a, never 0; exit 2 = not enough data."
        ),
        call=lambda repo, a, agent: _api().run_metrics(
            repo,
            view=a.get("view") or "summary",
            item=a.get("item") or "",
            phase=a.get("phase") or "",
            since=a.get("since") or "",
            group_by=a.get("group_by") or "",
            agent=agent,
        ),
        payload=("view", "now", "phase", "since", "metrics", "text"),
        render=_text,
        params=(
            Param(
                "view",
                help="summary (default), forecast, retro, skips, lanes, stages, locks, tiny, paths, variants, swarm or slo.",
                cli_help="summary (default), forecast, retro <id>, skips, lanes, stages, locks, tiny, paths, variants, swarm or slo",
                positional=True,
                nargs="?",
                choices=VIEWS,
                default=None,
            ),
            Param(
                "item",
                help="The item to look back on; with view=retro.",
                cli_help="with retro: the item id",
                positional=True,
                nargs="?",
                default=None,
            ),
            Param(
                "phase",
                help="Limit to one phase (and, for forecast, its critical path).",
                cli_help="limit to one phase",
                default="",
            ),
            Param(
                "since",
                help="Only what happened since: a duration back from now (7d, 12h) or a date YYYY-MM-DD.",
                cli_help="only since a duration back (7d, 12h) or a date YYYY-MM-DD",
                default="",
            ),
            Param(
                "group_by",
                help="Group the outcome rates by gate (default), reviewer, agent, kind or phase; with stages: phase, kind, size or agent.",
                cli_help="group outcome rates by gate (default), reviewer, agent, kind or phase; stages: phase, kind, size, agent",
                default="",
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
