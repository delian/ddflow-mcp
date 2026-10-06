"""B-split-cli-parser: `cli.py` is `main()`, the global options and registration -- each
command group's parser lives in its own module in `surfaces/parsers/`.

A feature that adds a subcommand edits its group's module, not a 1,300-line function every
other feature also edits. These tests keep it that way.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import pkgutil
from pathlib import Path

from ddflow.surfaces import cli, parsers
from ddflow.surfaces.parsers import REGISTER_ORDER

CLI = Path(cli.__file__)
#: cli.py was 1,705 lines with the whole parser in it; the split left about 350.
MAX_CLI_LINES = 400


def _top_level(parser: argparse.ArgumentParser) -> list[str]:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return list(sub.choices)


def test_cli_py_stays_small():
    n = len(CLI.read_text("utf-8").splitlines())
    assert n < MAX_CLI_LINES, f"cli.py is {n} lines; parser code belongs in surfaces/parsers/"


def test_cli_py_adds_no_subcommand_itself():
    tree = ast.parse(CLI.read_text("utf-8"))
    calls = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("add_parser", "add_subparsers")
    ]
    # The ONE add_subparsers is the root's; every add_parser is in a group module.
    assert len(calls) == 1, f"cli.py builds subcommands at lines {calls}"


def test_every_group_module_registers_and_is_registered():
    groups = [
        importlib.import_module(f"{parsers.__name__}.{m.name}")
        for m in pkgutil.iter_modules(parsers.__path__)
        if not m.name.startswith("_")
    ]
    assert {g.register for g in groups} == set(REGISTER_ORDER)


def test_each_subcommand_comes_from_exactly_one_group_in_help_order():
    """Registered cumulatively, as `build_parser` does: a later group may extend an earlier
    group's parser (the list viewers add `task list`), never re-add a top-level command."""
    root = argparse.ArgumentParser(prog="ddflow")
    s = root.add_subparsers(dest="cmd")
    seen: list[str] = []
    for register in REGISTER_ORDER:
        register(s)
        mine = [c for c in s.choices if c not in seen]
        assert mine, f"{register.__module__} registers no subcommand of its own"
        seen += mine
    assert seen == _top_level(cli.build_parser())
