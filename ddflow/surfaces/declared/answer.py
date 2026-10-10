"""The parameters every add command carries to answer the duplicate check
(`api/_dedupe.py`, decision D-no-duplicates), declared once.

On the command line the answer is the flags ``--new --extends ID --duplicate-of ID
--related ID --check`` (`dedupe_flags` reads them); over MCP it is ``relation`` and
``check_only``. `ANSWER_PARAMS` is both, for `Command.params`: the flags are CLI-only and
the two arguments MCP-only, in the order each surface has always listed them.
"""

from __future__ import annotations

from typing import Any

from ..registry import Param

#: At most one of the flags is given.
_ONE = "answer"

#: The CLI half. `tests/test_record_surface.py` compares these with `dedupe_flags.add_flags`.
ANSWER_CLI_PARAMS: tuple[Param, ...] = (
    Param(
        "new",
        type="boolean",
        help="answer a possible-duplicate refusal: this is a different record, file it",
        cli_only=True,
        exclusive=_ONE,
    ),
    Param(
        "extends",
        default="",
        metavar="ID",
        exclusive=_ONE,
        help="answer it: this adds to record ID -- appended to ID while it is open and "
        "unclaimed, else filed as a new record linked to it",
        cli_only=True,
    ),
    Param(
        "duplicate_of",
        default="",
        metavar="ID",
        exclusive=_ONE,
        help="answer it: this is the same thing as record ID (handled like --extends)",
        cli_only=True,
    ),
    Param(
        "related",
        default="",
        metavar="ID",
        exclusive=_ONE,
        help="answer it: a different record that is related to ID; linked both ways",
        cli_only=True,
    ),
    Param(
        "check",
        type="boolean",
        help="dry run: print the existing records this would be refused as a duplicate "
        "of and write nothing (exit 0 with candidates, 2 with none)",
        cli_only=True,
        exclusive=_ONE,
    ),
)

#: The MCP half, as the tool table has always carried it (`tools.DEDUPE_PROPERTIES`).
ANSWER_TOOL_PARAMS: tuple[Param, ...] = (
    Param(
        "relation",
        help="Answer to a 'possible duplicate' refusal: new | extends:ID | duplicate_of:ID | "
        "related:ID (the refusal lists candidates and options). Omit at first.",
        mcp_only=True,
    ),
    Param(
        "check_only",
        type="boolean",
        help="Dry run: write nothing, return the `candidates` this add would be refused for.",
        mcp_only=True,
    ),
)

ANSWER_PARAMS: tuple[Param, ...] = (*ANSWER_CLI_PARAMS, *ANSWER_TOOL_PARAMS)

#: What an add tool omits of the CLI's flags, for `Command.flag_exempt` (the parity test).
ANSWER_FLAG_EXEMPT: dict[str, str] = {
    f"--{name}": "the duplicate-check answer: MCP `relation` / `check_only`"
    for name in ("new", "extends", "duplicate-of", "related", "check")
}


def candidate_lines(rows: list[dict[str, Any]], *, numbered: bool = False) -> list[str]:
    lines = []
    for i, r in enumerate(rows, 1):
        flags = f"  [{', '.join(r['flags'])}]" if r.get("flags") else ""
        lead = f"{i}. " if numbered else ""
        # A bug may carry a severity and a scope (`bug found --severity --scope`).
        marks = "".join(
            f"  {r[k]}" for k in ("severity", "scope") if r.get(k) not in (None, "", "project")
        )
        lines.append(
            f"{lead}{r['id']}  {r['kind']}  {r['score']:.2f}  {r['state']}{marks}{flags}"
            f"\n    {r['title']}"
        )
        if r.get("shared"):
            lines[-1] += f"\n    shares: {', '.join(r['shared'])}"
    return lines
