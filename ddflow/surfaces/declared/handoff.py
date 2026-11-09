"""The `suggest` command, declared once."""

from __future__ import annotations

from ..registry import Command, Param, by_tool


def _suggest(repo, a, agent):
    """`ddflow_suggest`. The sender's own item/agent is the operator's, at a terminal."""
    from ...api import handoff as H

    return H.suggest(
        repo,
        item=a.get("item", "") or "",
        reason=a.get("reason", "") or "",
        kind=a.get("kind", "") or "",
        commit=a.get("commit", "") or "",
        diff=a.get("diff", "") or "",
        blob=a.get("blob", "") or "",
        evidence=a.get("evidence", "") or "",
        agent=agent,
    )


def _suggest_text(out, a) -> str:
    d = out.data
    if out.state == "failed":
        return f"refused: {d.get('summary') or d.get('text')}"
    return d.get("text") or d.get("summary") or ""


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("suggest",),
        tool="ddflow_suggest",
        summary="send a bounded change to the lease holder of an item, as a suggestion",
        description=(
            "Suggest a change to the agent that holds the item's lease: cut your patch onto their "
            "tip (commit-tree plumbing), probe it, record the suggestion, and hand the owner an "
            "answer pointer. Exactly one of --commit/--diff/--blob names the change."
        ),
        params=(
            Param(
                "item",
                positional=True,
                required=True,
                help="The item whose lease holder the suggestion goes to.",
                cli_help="the item whose lease holder receives the suggestion",
            ),
            Param(
                "reason",
                required=True,
                help="Why the owner should take it.",
                cli_help="why the owner should take it",
            ),
            Param(
                "commit",
                default="",
                help="A revision whose diff is the suggestion.",
                cli_help="a revision: its diff is the suggestion",
            ),
            Param(
                "diff",
                default="",
                help="A path to a `git diff` body.",
                cli_help="a file holding the git diff to suggest",
            ),
            Param(
                "blob",
                default="",
                help="A path to a raw patch text.",
                cli_help="a file holding the patch text to suggest",
            ),
            Param(
                "kind",
                default="",
                help="Optional kind (e.g. gate_fix).",
                cli_help="optional suggestion kind",
            ),
            Param(
                "evidence",
                default="",
                help="For gate_fix: CMD,EXIT the owner's re-run must reproduce.",
                cli_help="gate_fix: CMD,EXIT the re-run must reproduce",
            ),
        ),
        tool_order=("item", "reason", "commit", "diff", "blob", "kind", "evidence"),
        call=_suggest,
        payload=("ref", "probe", "blob"),
        render=_suggest_text,
    ),
)

BY_TOOL = by_tool(COMMANDS)
