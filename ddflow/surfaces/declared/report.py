"""The `report` command, declared once. CLI-ONLY for now: a read of the log that an agent ends
its run with, `ddflow report ITEM`; its MCP face costs tools/list bytes the budget does not have
(DX-report-mcp-tool)."""

from __future__ import annotations

from ..registry import Command, Param
from ..tools._common import _api


def _text(out, a) -> str:
    return out.data["text"].rstrip("\n")


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("report",),
        summary="the completion report of an item, from the log (shas, tests, gates, overrides)",
        tool="",
        reason=(
            "CLI-only: tools/list is within a few bytes of its budget; an MCP agent reads the "
            "same facts through ddflow_show and ddflow_gate_status"
        ),
        call=lambda repo, a, agent: _api().report(
            repo, item=a["item"], by_agent=a.get("by_agent") or "", agent=agent
        ),
        payload=(
            "item",
            "landing",
            "regression",
            "gates",
            "overrides",
            "filed",
            "follow_ups",
            "not_done",
        ),
        render=_text,
        params=(
            Param("item", help="", cli_help="the item to report on", positional=True),
            Param(
                "by_agent",
                flag="--agent",
                metavar="AGENT",
                help="",
                cli_help="count as filed by this agent only (default: the agent that completed it)",
                default="",
            ),
        ),
    ),
)
