"""`correct` -- replace a wrongly recorded value, history kept (REC-correct-surface).

Declared once: the CLI verb `ddflow correct` and the MCP action `ddflow_task action=correct`
(the old-name tool `ddflow_correct` stays callable as a hidden alias; the family adds no
top-level tool, so the `tools/list` byte budget is untouched). The writer and the permission
model are `api/record_correct.py` (D-correction-permissions-2).
"""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...core.outcome import OK
from ..registry import CliPolicy, Command, Param, by_tool
from ..tools._common import _answer, _api  # noqa: F401 -- _api names the api package lazily


def _text_of(a: dict, nearest: str) -> tuple[str | None, str]:
    """(text, why not): the new text, from `--text` or read from `--from-file`."""
    path = a.get("from_file") or ""
    if path and a.get("text") is not None:
        return None, f"give --text or --from-file, not both. {nearest}"
    if path:
        try:
            return Path(path).read_text(), ""
        except (OSError, ValueError) as exc:  # ValueError: bytes that are not text
            why = getattr(exc, "strerror", "") or exc
            return None, f"cannot read {path} as text: {why}. {nearest}"
    if a.get("text") is None:
        return None, f"give the new text: --text TEXT or --from-file PATH. {nearest}"
    return str(a["text"]), ""


def correct_call(repo, a: dict, agent: str):
    from ...api import record_correct as RC

    text, why = _text_of(a, RC.NEAREST)
    if text is None:
        return O.failed(RC.VERB, why)
    return RC.correct_one(
        repo,
        a["id"],
        field=a.get("field", "") or "",
        text=text,
        reason=a.get("reason", "") or "",
        kind=a.get("kind", "") or "",
        secret=bool(a.get("secret")),
        approval=a.get("approval", "") or "",
        agent=agent,
    )


def correct_text(out, a) -> str:
    d = out.data
    return (
        f"corrected {d['record_kind']} {d['id']}: {', '.join(d['fields'])} (reason: {d['why']})\n"
        f"  {d['follow_ups']}"
    )


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("correct",),
        summary="correct a wrongly recorded value, history kept",
        tool="ddflow_correct",
        tier="all",
        family="ddflow_task",
        action="correct",
        description=(
            "Correct a value recorded wrongly in a bug, lesson, research note, decision, memory, "
            "session, task or phase (also a completion's author model: field author_model) by "
            "writing a `record.corrected` event; the record then shows the new text, with an "
            "audit entry of who, when and why. NOT a removal: the old text stays in the event "
            "log, and a leaked secret needs a physical scrub of the log (a person's act) and rotation at "
            "its source. An agent corrects a record it authored and a record on an item it holds a "
            "lease on; anything else needs the operator (approval=request, the person answers "
            "in chat, then re-run with the approval id). The reason says why it was wrong, "
            "never what it was. The new text is redacted as every write is. `secret` keeps "
            "digests of the values out of the audit entry."
        ),
        flag_exempt={
            "--from-file": "reads a local file; over MCP the new text is passed as `text`"
        },
        call=correct_call,
        payload=("id", "record_kind", "fields", "why", "old_text_in_log", "follow_ups"),
        render=correct_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", help="The record to correct.", cli_help="", positional=True),
            Param(
                "field",
                help="The field to replace: title, body, tags, ... or a kind's own (summary, rule, why, author_model).",
                cli_help="the field to replace (body, title, summary, author_model, ...)",
                required=True,
            ),
            Param(
                "text",
                help="The new text.",
                cli_help="the new text (or --from-file)",
                default=None,
                exclusive="source",
                tool_required=True,
            ),
            Param(
                "from_file",
                help="",
                cli_help="read the new text from this file",
                default="",
                cli_only=True,
                exclusive="source",
            ),
            Param(
                "reason",
                help="Why the value was wrong. Required. Never the old value.",
                cli_help="why it was wrong (required; never the old value)",
                required=True,
            ),
            Param(
                "kind",
                help="The record kind, when the id names more than one.",
                cli_help="the record kind, when the id names more than one",
                default="",
            ),
            Param(
                "secret",
                type="boolean",
                help="The value may be a secret: keep every digest of it out of the audit entry.",
                cli_help="the value may be a secret: keep digests of it out of the audit entry",
            ),
            Param(
                "approval",
                help="An agent correcting another agent's record: 'request' asks the operator, then the approval id they answered.",
                cli_help="an agent: 'request' files an approval request (the operator answers in chat), then the approval id; a person corrects directly",
                default="",
            ),
        ),
        # An id no record holds: both surfaces refuse the same way and write nothing, so the
        # wire-shape check can run the writer twice.
        wire_shape=(
            ["correct", "NOPE", "--field", "body", "--text", "x", "--reason", "r"],
            {"id": "NOPE", "field": "body", "text": "x", "reason": "r"},
        ),
        tool_order=("id", "field", "text", "reason", "kind", "secret", "approval"),
    ),
)


BY_TOOL = by_tool(COMMANDS)
