"""The `lanes` command, declared once: the lane ledger, a lane's brief and the advisor.

`surfaces/parsers/lanes.py` registers the command-line half and `surfaces/tools/__init__.py`
takes the MCP entry (D-unify 4). Read-only by construction except `view=approve`, a person's
act that records a lane-template approval and spawns no process, kills nothing and starts no
daemon (D-sched-no-daemon). Acting on a lane (starting, resuming) is a different command
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
        summary="the lane ledger: each agent's state; `lanes brief <lane>`, `lanes advise`, `lanes lead`",
        tool="ddflow_lanes",
        description=(
            "Agent lanes from the log, read-only: harness, model, item, heartbeat and state "
            "(working, stale, waiting-on-glob naming the holder, idle-with-ready-work, "
            "stopped). view=brief + lane: that lane's whole first prompt. view=advise: "
            "concrete moves. view=lead: states with each lane's honest ETA and your pending "
            "asks. Never starts or stops an agent. Exit 2: nothing to show."
        ),
        call=lambda repo, a, agent: _api().lanes(
            repo,
            view="coordinator" if a.get("coordinator") else (a.get("view") or "state"),
            lane=(a.get("item") or "") or a.get("lane") or "",
            agent=agent,
        ),
        payload=(
            "view",
            "now",
            "lanes",
            "lane",
            "brief",
            "moves",
            "limit",
            "text",
            "fleet",
            "fleet_total",
            "resume",
            "coordinator",
        ),
        render=_text,
        params=(
            Param(
                "view",
                help="state (default), brief, advise (concrete moves), lead (states with ETA and your pending asks), fleet (every agent seen), resume (how to resume lane) or approve <template> (a person approves a lane template).",
                cli_help="state (default), brief <lane>, advise, lead, fleet, resume <lane> or approve <template>",
                positional=True,
                nargs="?",
                choices=(
                    "state",
                    "brief",
                    "advise",
                    "fleet",
                    "resume",
                    "lead",
                    "approve",
                    "coordinator",
                ),
                default=None,
            ),
            Param(
                "lane",
                help="Lane name (or a unique part); with view=brief, resume or approve.",
                cli_help="with brief, resume or approve: the lane name (for approve, the template name), or a unique part of it",
                positional=True,
                nargs="?",
                default=None,
            ),
            Param(
                "coordinator",
                type="boolean",
                help="The coordinator's screen: running suites, lease holders (idle ones flagged), pending approvals, ready work per phase and, with item, a blocked-by chain.",
                cli_help="the coordinator's screen: suites, lease holders, pending approvals, ready work per phase",
            ),
            Param(
                "item",
                help="With coordinator: an item whose blocked-by chain to show.",
                cli_help="with --coordinator: an item whose blocked-by chain to show",
                default="",
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
