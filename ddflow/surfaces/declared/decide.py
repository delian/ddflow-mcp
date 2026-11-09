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
        append=bool(a.get("append")) if a.get("append") is not None else False,
        body=a.get("body") or "",
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
            Param(
                "append",
                type="boolean",
                default=False,
                help="Keep a document that already exists instead of replacing it.",
                cli_help="append to the document instead of replacing it (keeps an existing doc)",
            ),
            Param(
                "body",
                help="The operator's own words to append to the restated record (e.g. a Q-and-A table).",
                cli_help="the body to append below the restated record (no second merge needed)",
            ),
            Param("model", default="", cli_help="the author's model family"),
        ),
    ),
)
