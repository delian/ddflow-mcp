"""`phase complete` -- the deliberate retire of a phase whose tasks are all settled.

The command is declared here, not in `queue.py`, to keep that file under the size cap: a
declared family is a module with a `COMMANDS` tuple, and `surfaces/parsers/queue.py` adds
it to the `phase` group the same way it adds `batch`'s.
"""

from __future__ import annotations

from ...core.outcome import OK
from ..registry import CliPolicy, Command, Param, by_tool
from ..tools._common import _api
from . import queue_cli as L

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("phase", "complete"),
        summary="retire a phase whose every task is done or abandoned",
        tool="ddflow_phase_complete",
        tier="all",
        family="ddflow_task",
        action="phase_complete",
        description="Retire a phase this project worked whose every task is done or "
        "abandoned, for a person directly and for an agent only by an operator "
        "approval of exactly this settled state. Verifies no task under the phase is "
        "still open (it refuses, naming them), records `phase.completed` with the "
        "phase id, its item count and who completed it, and runs NO phase pipeline. "
        "Different from `phase close`, which is the door for work that predates "
        "ddflow (imported).",
        call=lambda repo, a, agent: _api().phase_complete(
            repo,
            a["id"],
            approval=a.get("approval", "") or "",
            reason=a.get("reason", "") or "",
            agent=agent,
        ),
        payload=("id", "evidence", "tasks", "done", "abandoned"),
        render=L.phase_complete_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", help="The phase to retire.", cli_help="", positional=True),
            Param(
                "approval",
                help="An agent's route: 'request' asks the operator, then the approval id they answered.",
                cli_help="an agent: 'request' files an approval request (the operator answers in chat), then the approval id; a person completes directly",
                default="",
            ),
            Param(
                "reason",
                help="An agent's own words about this retire, quoted to the operator.",
                cli_help="an agent's own words about this retire, quoted to the operator (untrusted)",
                default="",
            ),
        ),
        tool_order=("id", "approval", "reason"),
    ),
)


BY_TOOL = by_tool(COMMANDS)
