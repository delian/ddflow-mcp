"""The `lanes` command, declared once: the lane ledger, a lane's brief and the advisor.

`surfaces/parsers/lanes.py` registers the command-line half and `surfaces/tools/__init__.py`
takes the MCP entry (D-unify 4). Read-only by construction: it never spawns or kills a
process and starts no daemon (D-sched-no-daemon). Acting on a lane (starting, resuming) is a different command
(P-agent-parity.launcher).
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api


def _text(out, a) -> str:
    """The human form is built by the api (`text`; a brief is its own text), shared by both."""
    return out.data["text"] or out.data["brief"].rstrip("\n")


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("lanes",),
        summary="the lane ledger: each agent's state; `lanes brief <lane>`, `lanes advise`",
        tool="ddflow_lanes",
        description=(
            "Agent lanes from the log, read-only: harness, model, item, heartbeat and state "
            "(working, stale, waiting-on-glob naming the holder, idle-with-ready-work, "
            "stopped). view=brief + lane: that lane's whole first prompt. view=advise: "
            "concrete moves. Never starts or stops an agent. Exit 2: nothing to show."
        ),
        call=lambda repo, a, agent: _api().lanes(
            repo, view=a.get("view") or "state", lane=a.get("lane") or "", agent=agent
        ),
        payload=("view", "now", "lanes", "lane", "brief", "moves", "limit", "text"),
        render=_text,
        params=(
            Param(
                "view",
                help="state (default), brief or advise.",
                cli_help="state (default), brief <lane> or advise",
                positional=True,
                nargs="?",
                choices=("state", "brief", "advise"),
                default=None,
            ),
            Param(
                "lane",
                help="Lane name (or a unique part); with view=brief.",
                cli_help="with brief: the lane name, or a unique part of it",
                positional=True,
                nargs="?",
                default=None,
            ),
            Param(
                "watch",
                type="boolean",
                help="",
                cli_help="redraw when the log changes or every [lanes] watch_interval_s",
                cli_only=True,
            ),
        ),
        flag_exempt={"--watch": "a blocking redraw loop: an MCP call answers once"},
    ),
)

BY_TOOL = by_tool(COMMANDS)
