"""The `report` command, declared once. A read of the log that an agent ends its run with. It has
no tool of its own (tools/list is at its byte budget): over MCP it is `ddflow_show report=true`."""

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
            "tools/list is at its byte budget: over MCP the same report is ddflow_show with report=true"
        ),
        call=lambda repo, a, agent: _api().item_report(
            repo,
            item=a["item"],
            by_agent=a.get("by_agent") or "",
            agent=agent,
            brief=bool(a.get("brief")),
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
            Param(
                "brief",
                type="boolean",
                help="",
                cli_help="the 8-line form: landing sha, regression test, forced or skipped gates, filed ids, follow-ups, not done",
                default=False,
            ),
        ),
    ),
)
