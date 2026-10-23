"""The `grant` commands, declared once. CLI-ONLY: a grant is the operator's act, refused
under any agent identity like `ddflow approve`, so no tool carries it (and tools/list has
no bytes to spare)."""

from __future__ import annotations

from ..registry import Command, Param

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("grant", "add"),
        summary="a PERSON records a grant of extra review rounds (refused under an agent identity)",
        tool="",
        reason="a person's act: refused for every agent identity, so no tool carries it",
        params=(
            Param("item", required=True, cli_help="the item the grant is for"),
            Param("gate", required=True, cli_help="the gate it covers (rubber_duck, critic)"),
            Param("kind", default="review_round", cli_help="what is granted (review_round)"),
            Param(
                "count",
                type="integer",
                default=1,
                cli_help="how many rounds, up to [grants].max_count",
            ),
            Param("reason", required=True, cli_help="what the operator allowed and why"),
            Param(
                "approval",
                default="",
                cli_help="an agent: the id of the operator's approval of exactly this grant (a refusal files the request and names it)",
            ),
            Param(
                "when",
                default="",
                cli_help="make it STANDING: apply it mechanically when this predicate holds (tree_changed_after_triage)",
            ),
        ),
    ),
    Command(
        path=("grant", "list"),
        summary="every recorded grant: who, when, rounds used",
        tool="",
        reason="CLI-only with `grant add`: the operator's surface",
        params=(Param("item", default="", cli_help="only this item's grants"),),
    ),
)
