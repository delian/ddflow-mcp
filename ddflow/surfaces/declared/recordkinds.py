"""The `record` command, declared once: any data-declared record kind, one verb at a time.

`surfaces/parsers/recordkinds.py` registers the command-line half and `surfaces/tools/`
takes the MCP entry. ONE tool for every kind, so adding a kind adds nothing to `tools/list`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from ..registry import Command, Param, by_tool

VERBS = ("list", "show", "add", "edit", "remove", "search", "revise", "history")


def _rk() -> Any:
    """`surfaces.recordkinds`, imported when a call is made: it needs `surfaces.records`, which
    needs the registry this module is part of."""
    return import_module("ddflow.surfaces.recordkinds")


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("record",),
        summary="records of a data-declared kind: `record <kind> <verb> [id] [--set field=value]`",
        tool="ddflow_record",
        description=(
            "Records of a data kind (risk, vendor). verb: list show add edit remove search "
            "revise history. set: field=value; @flag=value gives an option (@reason=why, @query=text)."
        ),
        flag_exempt={
            f"--{n}": "the tool carries it as an @name=value item of set (its schema stays small)"
            for n in (
                "all",
                "check-only",
                "limit",
                "mode",
                "query",
                "reason",
                "relation",
                "unset",
                "where",
            )
        },
        call=lambda repo, a, agent: _rk().call(repo, a, agent),
        render=lambda out, a: _rk().text(out, a),
        params=(
            Param("kind", help="", cli_help="The record kind.", positional=True, required=True),
            Param(
                "verb",
                help="",
                cli_help=f"One of {', '.join(VERBS)}.",
                positional=True,
                required=True,
                choices=VERBS,
            ),
            Param(
                "id", help="", cli_help="The record's id.", positional=True, nargs="?", default=None
            ),
            Param("set", type="array", repeat=True, help="", cli_help="field=value; repeat."),
            *(
                Param(n, type=t, help="", cli_help=h, repeat=r, cli_only=True)
                for n, t, h, r in (
                    ("query", "string", "search: what to look for.", False),
                    ("reason", "string", "remove, revise: why; kept in the history.", False),
                    ("unset", "array", "Fields to remove (edit).", True),
                    ("mode", "string", "search: ranked | exact | regex.", False),
                    ("where", "array", "list, search: field=value.", True),
                    ("all", "boolean", "Include retired records.", False),
                    ("limit", "integer", "Rows shown (0 = the most).", False),
                    (
                        "relation",
                        "string",
                        "add: new | extends:ID | duplicate_of:ID | related:ID.",
                        False,
                    ),
                    ("check_only", "boolean", "add: dry run, return the candidates.", False),
                )
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
