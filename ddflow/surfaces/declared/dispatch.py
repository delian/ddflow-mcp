"""The `dispatch` command, declared once: the dispatcher's plan (DX-dispatch-plan).

`surfaces/parsers/` registers the command line half and `surfaces/tools/__init__.py`
takes the MCP entry. Read-only by construction unless `--tick` records the plan as one
audit note; it starts nothing (D-sched-no-daemon).
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool


def _call(repo, a, agent):
    """Import the read half lazily, inside the call (the api reaches jinja2)."""
    from ...api.dispatch import dispatch

    return dispatch(
        repo,
        mode="tick" if a.get("tick") else "plan",
        phase=a.get("phase") or "",
        agent=agent,
    )


def _text(out, a) -> str:
    """Both surfaces share the api's `text`."""
    return out.data["text"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("dispatch",),
        summary="the dispatcher's plan: what to start now, in order, on which lane (advise only)",
        tool="ddflow_dispatch",
        description=(
            "Compute which queue items to start right now from the free slots, the ready "
            "set, conflicts and pin waits: the ordered items (bug fixes first, then "
            "priority), each with the lane to run it, its globs and why; and the lanes to "
            "RESUME, YIELD or NUDGE. Advise only, idempotent. tick=true also records one "
            "audit note. Never starts or claims an agent."
        ),
        call=_call,
        payload=("mode", "free", "limit", "starts", "moves", "text"),
        render=_text,
        params=(
            Param(
                "tick",
                type="boolean",
                help="Record the plan as one audit note (advise + audit).",
                cli_help="record one audit note (advise + audit)",
            ),
            Param(
                "phase",
                help="Restrict the ready set to this phase (optional).",
                cli_help="restrict the ready set to this phase",
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
