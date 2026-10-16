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
        summary="stalled, locked, crashed and runaway lanes; `--run` is the periodic watchdog pass",
        tool="ddflow_health",
        description=(
            "The watchdog. Inspects every lane for STALLED (current lease, no progress), "
            "LOCKED (in a wait cycle, or waiting on a stalled lane), CRASHED (lease expired over "
            "work) and RUNAWAY (a job running past [health].runaway_s), each with evidence and the "
            "command that corrects it. run=true is the periodic pass: it also makes the "
            "corrections [health].mode allows (off | propose | apply; apply breaks a stalled "
            "contended lease, adds auto-order needs edges, records a crashed lane recoverable) "
            "and records the run for `cadence`. It never kills a process or deletes work."
        ),
        call=lambda repo, a, agent: _api().health(
            repo, run=bool(a.get("run")), mode=a.get("mode") or "", agent=agent
        ),
        payload=("mode", "ran", "now", "findings", "moves", "count", "text"),
        render=_text,
        params=(
            Param(
                "run",
                type="boolean",
                help="Also make the corrections [health].mode allows and record the pass.",
                cli_help="the periodic pass: correct per mode and record it for `cadence`",
            ),
            Param(
                "mode",
                help="Override [health].mode for this call: off, propose or apply.",
                cli_help="override [health].mode: off, propose or apply",
                choices=("off", "propose", "apply"),
                default=None,
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
