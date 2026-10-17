"""The `decide` command, declared once. CLI-ONLY (tools/list has no headroom): it closes a
decide task from its decision record -- writes the document, commits, lands, completes."""

from __future__ import annotations

from ...api.decide import decide
from ..registry import CliPolicy, Command, Param

PAYLOAD = ("item", "decision", "path", "sha", "skipped_gates", "stage")


def _call(repo, a, agent, called_from=None, shell_cwd=None):
    return decide(
        repo,
        a["id"],
        decision_id=a.get("decision_id") or "",
        path=a.get("path") or "",
        model=a.get("model") or "",
        agent=agent,
        called_from=called_from,
    )


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("decide",),
        summary="close a decide task: write its decision document from the record, land, complete",
        tool="",
        reason="CLI-only: tools/list has no headroom; a ceremony an agent runs from its shell",
        call=_call,
        payload=PAYLOAD,
        render=lambda out, a: out.data.get("text") or out.reason,
        cli=CliPolicy(shown=(0,)),
        wants_called_from=True,
        params=(
            Param("id", positional=True, help="The decide task.", cli_help="the decide task"),
            Param(
                "decision_id",
                required=True,
                help="The recorded decision.",
                cli_help="the recorded decision (D-x) whose document is written",
            ),
            Param(
                "path",
                help="The document path.",
                cli_help="the document (default docs/ddflow/decisions/<slug>.md)",
            ),
            Param("model", default="", cli_help="the author's model family"),
        ),
    ),
)
