"""The `health` command, declared once: the watchdog's view and its periodic pass.

`surfaces/parsers/health.py` registers the command-line half and `surfaces/tools/__init__.py`
takes the MCP entry (D-unify 4). It never kills a process and never deletes work.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _text(out, a) -> str:
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("vigil",),
        summary="one compact health snapshot: bug severity counts, stale pins, active lanes, interrupted, pending approvals, reviewer up/429/slow",
        tool="ddflow_vigil",
        tier="all",
        family="ddflow_view",
        action="vigil",
        description=(
            "One compact health snapshot in one pass: open bugs by severity + fixed in the "
            "window, stale pins, active lanes with their items and lane age, the interrupted "
            "count, pending approvals, and each reviewer's reachability -- up (answering), 429 "
            "(its own quota/exhaustion wall) or slow (answering while the fleet latency ratio "
            "is above 1x), and down (nothing answering) -- with that ratio as the 'slow' "
            "field. Reuses the other verbs (bug list, pins --check, lanes, approvals pending, "
            "reviewers test) rather than reimplementing them. Read-only."
        ),
        call=lambda repo, a, agent: _api().reporting.vigil_snapshot(repo, agent=agent),
        payload=(
            "bugs",
            "stale_pins",
            "lanes",
            "lanes_total",
            "interrupted",
            "approvals_pending",
            "pending_approval_ids",
            "reviewers",
            "reviewer_latency_ratio",
            "text",
        ),
        render=_text,
    ),
    Command(
        path=("health",),
        summary="idle, stalled, unresponsive, locked, crashed and runaway lanes; `--run` is the periodic watchdog pass",
        tool="ddflow_health",
        description="Watchdog: idle, stalled, unresponsive, locked, crashed, runaway lanes and their fixes; run=true corrects per [health].mode and the mode_<kind> overrides.",
        call=lambda repo, a, agent: _api().health(
            repo, run=bool(a.get("run")), mode=a.get("mode") or "", agent=agent
        ),
        payload=("mode", "modes", "ran", "findings", "moves", "count", "text"),
        render=_text,
        flag_exempt={
            "--mode": "per-call override of every health mode; over MCP set [health].mode / mode_<kind> with ddflow_configure"
        },
        params=(
            Param(
                "run",
                type="boolean",
                help="Correct per mode.",
                cli_help="the periodic pass: correct per mode and record it for `cadence`",
            ),
            Param(
                "mode",
                help="",
                cli_help="override every health mode (mode and mode_<kind>) for this call: off, propose or apply",
                choices=("off", "propose", "apply"),
                default=None,
                cli_only=True,
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
