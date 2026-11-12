"""Completion conformance: the engine offers exactly what the parser declares, a
synthetic command completes with zero completion-code changes, and every shipped shell
script is syntactically valid.

Three contracts COMP-conformance owns:

* *differential* -- for every command path of the real parser, the engine (the command
  tree read off that parser) offers exactly the parser's subcommands, options and choices:
  nothing missing, nothing extra.
* *canary* -- a command, option, choice and id-like parameter registered only through the
  generic registry, plus one declared record kind, are completed with no change to any
  ``ddflow/services/completion/*`` module.
* *shell* -- each activation template parses under its own shell (``bash -n``, ``zsh -n``,
  ``fish -n``); a shell that is not installed is a coverage gap, not a failure.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ddflow.services.completion import kinds
from ddflow.services.completion import scripts
from ddflow.services.completion import tree as T
from ddflow.services.completion import values as V
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.registry import Command, Param, SuggestingParser, add_commands


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return build_parser()


@pytest.fixture(scope="module")
def tree(parser):
    return T.command_tree(parser)


def _parsers(parser, path=()):
    """Every canonical command path of the real parser, by argparse's own iteration."""
    yield path, parser
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, child in a.choices.items():
                yield from _parsers(child, (*path, name))


def _subcommand_names(parser) -> list[str]:
    out: list[str] = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            out.extend(name for name, _child in a.choices.items())
    return out


def _visible_flags(parser, root) -> set[str]:
    """The option strings the tree offers for ``parser``: its own options, a SUPPRESS one
    only when it mirrors an ancestor's (global) option, exactly as the engine walks it."""
    mirrored = {s for a in root._actions for s in a.option_strings}
    out: set[str] = set()
    for a in parser._actions:
        if not a.option_strings:
            continue
        if a.help == argparse.SUPPRESS and not set(a.option_strings) & mirrored:
            continue
        out.update(a.option_strings)
    return out


def test_the_engine_offers_exactly_the_parsers_surface(parser, tree):
    """Every command path offers exactly the parser's subcommands, options and choices."""
    paths = dict(_parsers(parser))
    walked = dict(T.walk(tree))
    assert set(walked) == set(paths)  # no command missing, none extra
    assert len(paths) > 50  # the real parser, at scale
    for path, p in paths.items():
        node = walked[path]
        # subcommands: the same names, in argparse's own order
        assert list(node["commands"]) == _subcommand_names(p), path
        # options: exactly the parser's visible flags -- a dropped option would leave a
        # flag out of the set, an extra one a flag in (nothing missing, nothing extra)
        offered = {f for o in node["options"] for f in o["flags"]}
        assert offered == _visible_flags(p, parser), path
        # each offered option is one argparse action: its flags and choices are that
        # action's own (a multi-flag option still has exactly the action's choices)
        for o in node["options"]:
            action = p._option_string_actions[o["flags"][0]]
            assert list(o["flags"]) == list(action.option_strings), path
            assert o["choices"] == (
                None if action.choices is None else [str(c) for c in action.choices]
            ), path


def test_canary_a_synthetic_command_completes_with_no_completion_code(tmp_path):
    """A command, option, choice and id-like parameter registered through the generic
    registry only -- plus one declared record kind -- complete with zero edits to any
    completion module: the tree reads them off argparse, the engine infers them by name."""
    record_kind = "widget"
    assert record_kind not in kinds.id_kinds(), "the canary record kind must be new"
    try:
        kinds.declare_id_kind(record_kind)
        root = SuggestingParser(prog="x")
        add_commands(
            root.add_subparsers(dest="cmd", required=True),
            [
                Command(
                    path=("widget",),
                    summary="synthetic record verb",
                    params=(
                        Param("widget", positional=True),
                        Param("mode", choices=("a", "b")),
                    ),
                    handler=lambda a, c: 0,
                )
            ],
        )
        node = T.command_tree(root)["commands"]["widget"]
        # a new option and its choices complete (enum) with no per-command code
        opts = {o["flags"][0]: o for o in node["options"]}
        assert opts["--mode"]["choices"] == ["a", "b"]
        assert opts["--mode"]["kind"] == "enum"
        # a new id-like positional narrows to the declared record kind
        pos = [p for p in node["positionals"] if p["dest"] == "widget"]
        assert pos, "the synthetic id positional is offered"
        assert pos[0]["kind"] == "id:widget"
        assert pos[0]["arity"] == "1" and pos[0]["dest"] == "widget"
        # the engine COMPLETES the declared kind to its records, with no per-kind list
        assert V.complete("id:widget", "W", index=_widget_index(tmp_path)) == ["W1"]
    finally:
        kinds._id_kinds -= {record_kind}


def _widget_index(d: Path) -> str:
    con = sqlite3.connect(d / "index.db-wwwwwwww")
    con.execute("create table widgets(id text, status text)")
    con.execute("insert into widgets values('W1','live')")
    con.commit()
    con.close()
    return str(d / "index.db-wwwwwwww")


@pytest.mark.parametrize("shell", scripts.SHELLS)
def test_every_shell_script_parses(shell, tmp_path):
    """Each activation template parses under its own shell; an uninstalled shell is a
    coverage gap (unavailable), never a failure."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed: the {shell} template is unexercised")
    path = tmp_path / f"ddflow-completion.{shell}"
    path.write_text(scripts.for_shell(shell), encoding="utf-8")
    result = subprocess.run([exe, "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, (shell, result.stderr)
