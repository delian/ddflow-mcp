"""The `record` command, declared once: any data-declared record kind, one verb at a time.

`surfaces/parsers/recordkinds.py` registers the command-line half and `surfaces/tools/`
takes the MCP entry. ONE tool for every kind, so adding a kind adds nothing to `tools/list`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from ..registry import Command, Param, by_tool

VERBS = ("list", "show", "add", "edit", "remove", "search", "revise", "history")
MODES = ("ranked", "exact", "regex")


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
            "Records of any data-declared kind (risk, requirement, vendor ...; project kinds "
            "in .ddflow/kinds/). verb: list, show, add, edit, remove, search, revise, history. "
            "set: field=value, repeat for an array. add answers the duplicate check like every add."
        ),
        call=lambda repo, a, agent: _rk().call(repo, a, agent),
        render=lambda out, a: _rk().text(out, a),
        params=(
            Param("kind", help="The record kind.", positional=True, required=True),
            Param(
                "verb",
                help=f"One of {', '.join(VERBS)}.",
                positional=True,
                required=True,
                choices=VERBS,
                schema_enum=True,
            ),
            Param("id", help="The record's id.", positional=True, nargs="?", default=None),
            Param("set", type="array", repeat=True, help="field=value; repeat."),
            Param("unset", type="array", repeat=True, help="Fields to remove (edit)."),
            Param("query", help="search: what to look for."),
            Param("mode", help="search: ranked | exact | regex.", choices=MODES),
            Param("where", type="array", repeat=True, help="list, search: field=value."),
            Param("all", type="boolean", help="Include retired records."),
            Param("limit", type="integer", help="Rows shown (0 = the most)."),
            Param("reason", help="remove, revise: why; kept in the history."),
            Param("relation", help="add: new | extends:ID | duplicate_of:ID | related:ID."),
            Param("check_only", type="boolean", help="add: dry run, return the candidates."),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
