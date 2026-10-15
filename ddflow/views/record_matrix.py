"""Which verbs each kind of record offers on which surface, generated (P-core-records.matrix).

The matrix is the baseline and the ratchet of P-core-records: for every record kind (bug,
lesson, research, memory, task, phase, job, decision, rule, session) and every verb (list,
show, add, edit, note, link, remove, reopen, history, supersede, split, search) it says what
the CLI, the MCP server, the Python API and the harness hooks offer -- or that they offer
nothing, which is a gap.

Nothing here is a table of answers. A cell is PROBED against the live registries:

* the CLI: the parser's own leaf commands (``build_parser``), a flag on one, or a generic
  top-level command (``show``, ``update``, ``remove``, ``link``, ``history``, ``split``,
  ``list``) for the kinds it works on;
* MCP: ``TOOLS`` -- the tool of that name, the tool a CLI path is declared to be served by
  (``exemptions.ROUTED_PATHS``), or the generic tool;
* the Python API: the name exists in ``ddflow.api`` (a declared `RecordKind` offers
  ``record_<verb>`` for the verbs it declares);
* hooks: the hook entry points the harness calls, if they exist.

What this module declares is only vocabulary: the word a kind uses for a verb where it is not
the verb itself (a bug is ``found``, a memory ``forgotten``), and which kinds each generic
command works on. A command that is renamed or removed turns its cell into a gap; a command
that is added fills one. `tests/test_record_verb_matrix.py` pins today's gaps as a list that
may only shrink.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

KINDS = (
    "bug",
    "lesson",
    "research",
    "memory",
    "task",
    "phase",
    "job",
    "decision",
    "rule",
    "session",
)
VERBS = (
    "list",
    "show",
    "add",
    "edit",
    "note",
    "link",
    "remove",
    "reopen",
    "history",
    "supersede",
    "split",
    "search",
)
SURFACES = ("cli", "mcp", "api", "hooks")

#: The word a kind's own command uses for a verb, where it is not the verb.
WORDS: dict[tuple[str, str], str] = {
    ("bug", "add"): "found",
    ("bug", "remove"): "invalid",
    ("memory", "remove"): "forget",
    ("job", "remove"): "end",
    ("session", "add"): "start",
}
#: A kind whose add is a bare command, not ``<kind> add``.
BARE_PATH = {("research", "add")}
#: Where a verb is a flag on another verb of the kind: (command word, flag).
FLAGS: dict[tuple[str, str], tuple[str, str]] = {("lesson", "supersede"): ("add", "supersedes")}
#: The Python API's other names for a kind's verb.
API_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("rule", "edit"): ("rule_update",),
    ("rule", "show"): ("rule_get",),
    ("session", "add"): ("session_start",),
    ("lesson", "remove"): (),
}
#: What the harness's hook entry points do to records: the CLI path of each.
HOOKS: dict[tuple[str, str], tuple[str, ...]] = {
    ("session", "add"): ("hooks", "prompt"),
    ("session", "note"): ("hooks", "pre-compact"),
}


@dataclass(frozen=True)
class Generic:
    """A command that is not a kind's own but works on several kinds."""

    cli: tuple[str, ...]
    tool: str
    api: str
    kinds: tuple[str, ...]


_ITEMS = ("task", "phase")
_RECORDS = ("bug", "lesson", "research", "memory", "decision", *_ITEMS)
#: The verbs a top-level command answers, for the kinds it answers them for (what
#: `show`, `update`, `remove`, `link`, `history` and `split` take is in the lines cited in
#: the tests; ``list`` is read from the viewers' own table).
GENERIC: dict[str, Generic] = {
    "show": Generic(("show",), "ddflow_show", "show", ("bug", *_ITEMS)),
    "edit": Generic(("update",), "ddflow_update", "update", _ITEMS),
    "remove": Generic(("remove",), "ddflow_remove", "remove_item", _ITEMS),
    "link": Generic(("link",), "ddflow_link", "link_record", _RECORDS),
    "history": Generic(("history",), "ddflow_history", "history", _ITEMS),
    "split": Generic(("split",), "ddflow_split", "split", ("task",)),
}


def _parser() -> argparse.ArgumentParser:
    from ..surfaces.cli import build_parser

    return build_parser()


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def leaves() -> dict[tuple[str, ...], argparse.ArgumentParser]:
    """Every CLI command by its path (a group's subcommands, and a group that is a command
    in its own right, are both there)."""
    out: dict[tuple[str, ...], argparse.ArgumentParser] = {}

    def walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        subs = _subparsers(parser)
        if prefix:
            out[prefix] = parser
        for name, sub in subs.items():
            walk(sub, (*prefix, name))

    walk(_parser(), ())
    return out


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    return {o.lstrip("-") for a in parser._actions for o in a.option_strings}


class Probe:
    """The registries a cell is read from, loaded once."""

    def __init__(self) -> None:
        from .. import api
        from ..api import records as R
        from ..surfaces import exemptions as X
        from ..surfaces.commands import viewers_lists as L
        from ..surfaces.mcp import TOOLS

        self.cli = leaves()
        self.tools = TOOLS
        self.routed = X.ROUTED_PATHS
        self.api = api
        self.listed = tuple(L._LISTS)
        self.declared = R.KINDS

    def _word(self, kind: str, verb: str) -> str:
        return WORDS.get((kind, verb), verb)

    def _path(self, kind: str, verb: str) -> tuple[str, ...]:
        if (kind, verb) in BARE_PATH:
            return (kind,)
        return (kind, self._word(kind, verb))

    def cli_cell(self, kind: str, verb: str) -> str:
        if (kind, verb) in FLAGS:
            word, flag = FLAGS[(kind, verb)]
            parser = self.cli.get((kind, word))
            return f"{kind} {word} --{flag}" if parser and flag in _flags(parser) else ""
        path = self._path(kind, verb)
        if path in self.cli:
            return " ".join(path)
        if verb == "list" and kind in self.listed and ("list",) in self.cli:
            return f"list {kind}"
        gen = GENERIC.get(verb)
        if gen and kind in gen.kinds and gen.cli in self.cli:
            return " ".join(gen.cli)
        return ""

    def mcp_cell(self, kind: str, verb: str) -> str:
        tool = f"ddflow_{kind}_{self._word(kind, verb)}"
        if (kind, verb) in FLAGS:
            word, flag = FLAGS[(kind, verb)]
            props = (self.tools.get(f"ddflow_{kind}_{word}") or {}).get("properties", {})
            return f"{tool.rsplit('_', 1)[0]}_{word} ({flag})" if flag in props else ""
        if tool in self.tools:
            return tool
        via = self.routed.get(self._path(kind, verb))
        if via and via[0] in self.tools:
            return f"{via[0]} ({via[1]})"
        if verb == "list" and kind in self.listed and "ddflow_list" in self.tools:
            return f"ddflow_list ({kind})"
        gen = GENERIC.get(verb)
        if gen and kind in gen.kinds and gen.tool in self.tools:
            return gen.tool
        return ""

    def api_cell(self, kind: str, verb: str) -> str:
        word = FLAGS[(kind, verb)][0] if (kind, verb) in FLAGS else self._word(kind, verb)
        names = [f"{kind}_{word}", *API_NAMES.get((kind, verb), ())]
        via = self.routed.get(self._path(kind, verb))
        if via and via[0] == "ddflow_list":
            names.append("view_list")
        desc = self.declared.get(kind)
        if desc and verb in desc.verbs:
            names.insert(0, f"records.record_{verb}")
        if verb == "list" and kind in self.listed:
            names.append("view_list")
        gen = GENERIC.get(verb)
        if gen and kind in gen.kinds:
            names.append(gen.api)
        for name in names:
            if name.startswith("records."):
                return name
            if hasattr(self.api, name):
                return name
        return ""

    def hooks_cell(self, kind: str, verb: str) -> str:
        path = HOOKS.get((kind, verb))
        return " ".join(path) if path and path in self.cli else ""

    def cell(self, kind: str, verb: str, surface: str) -> str:
        return getattr(self, f"{surface}_cell")(kind, verb)


def matrix(probe: Probe | None = None) -> dict[tuple[str, str, str], str]:
    """(kind, verb, surface) -> what offers it there, "" when nothing does."""
    probe = probe or Probe()
    return {(k, v, s): probe.cell(k, v, s) for k in KINDS for v in VERBS for s in SURFACES}


def gaps(cells: dict[tuple[str, str, str], str] | None = None) -> list[tuple[str, str, str]]:
    """The (kind, verb, surface) cells where the verb is offered on SOME surface but not
    this one: a surface lacking what another has is the inconsistency the phase removes.
    A verb offered nowhere for a kind is a gap in the kind, listed by `unoffered`."""
    cells = cells or matrix()
    out = []
    for k in KINDS:
        for v in VERBS:
            have = [s for s in SURFACES if cells[(k, v, s)]]
            if have:
                out.extend((k, v, s) for s in SURFACES if s not in have and s != "hooks")
    return out


def unoffered(cells: dict[tuple[str, str, str], str] | None = None) -> list[tuple[str, str]]:
    """The (kind, verb) pairs no surface offers."""
    cells = cells or matrix()
    return [(k, v) for k in KINDS for v in VERBS if not any(cells[(k, v, s)] for s in SURFACES)]


def render(cells: dict[tuple[str, str, str], str] | None = None) -> str:
    """The matrix as Markdown: one row per kind, one column per verb; a cell lists the
    surfaces that offer it (``C`` CLI, ``M`` MCP, ``A`` API, ``H`` hooks) or ``-``."""
    cells = cells or matrix()
    mark = {"cli": "C", "mcp": "M", "api": "A", "hooks": "H"}
    head = "| kind | " + " | ".join(VERBS) + " |"
    rows = [head, "|---|" + "---|" * len(VERBS)]
    for k in KINDS:
        cols = ["".join(mark[s] for s in SURFACES if cells[(k, v, s)]) or "-" for v in VERBS]
        rows.append(f"| {k} | " + " | ".join(cols) + " |")
    return "\n".join(rows)


def main() -> int:
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
