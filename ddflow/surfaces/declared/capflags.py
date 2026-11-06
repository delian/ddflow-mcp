"""The `--full` / `--cursor` parameters of the capped read commands (UX-output-caps).

One declaration for all five, so the CLI flag and the MCP property cannot differ."""

from __future__ import annotations

from ..registry import Param

FULL = Param(
    "full",
    type="boolean",
    help="Lift the output caps.",
    cli_help="print everything: lift the default output caps ([output] max_rows, max_chars) for this call",
    default=False,
)
CURSOR = Param(
    "cursor",
    type="integer",
    help="Row to resume at.",
    cli_help="resume a cut list at this row (the number the previous footer gave)",
    default=0,
)


def capped(paged: bool) -> tuple[Param, ...]:
    """`--full`, and `--cursor` for a command whose list pages."""
    return (FULL, CURSOR) if paged else (FULL,)
