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
