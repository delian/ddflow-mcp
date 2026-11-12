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
import os
import re
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


def _visible(parser, inherited):
    """The engine's own visible options of ``parser``: a SUPPRESS option is kept only when
    it mirrors an ancestor's option (the global ``--json``/``--agent`` copies)."""
    mirrored = {s for a in inherited for s in a.option_strings}
    for a in parser._actions:
        if not a.option_strings:
            continue
        if a.help == argparse.SUPPRESS and not set(a.option_strings) & mirrored:
            continue
        yield a


def _parsers(parser, inherited=(), path=()):
    """Every canonical command path of the real parser with the options its ancestors
    contributed, so the expected option set derives the same way the engine's walk does."""
    yield path, parser, inherited
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            seen = tuple(inherited) + tuple(_visible(parser, inherited))
            for name, child in a.choices.items():
                yield from _parsers(child, seen, (*path, name))


def _subcommand_names(parser) -> list[str]:
    out: list[str] = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            out.extend(name for name, _child in a.choices.items())
    return out


def _positionals(parser) -> list[argparse.Action]:
    return [
        a
        for a in parser._actions
        if not a.option_strings and not isinstance(a, argparse._SubParsersAction)
    ]


def test_the_engine_offers_exactly_the_parsers_surface(parser, tree):
    """Every command path offers exactly the parser's subcommands, options and choices,
    for options and positionals alike: nothing missing, nothing extra."""
    paths = {path: (p, inh) for path, p, inh in _parsers(parser)}
    walked = dict(T.walk(tree))
    assert set(walked) == set(paths)  # no command missing, none extra
    assert len(paths) > 50  # the real parser, at scale
    for path, (p, inherited) in paths.items():
        node = walked[path]
        # subcommands: the same names, in argparse's own order
        assert list(node["commands"]) == _subcommand_names(p), path
        # options: exactly the engine's visible flags -- a dropped option would leave a
        # flag out of the set, an extra one a flag in (nothing missing, nothing extra)
        actions = list(_visible(p, inherited))
        offered = {f for o in node["options"] for f in o["flags"]}
        expected = {s for a in actions for s in a.option_strings}
        assert offered == expected, path
        # one option entry per action: a duplicate entry would change the length
        assert len(node["options"]) == len(actions), path
        # each offered option is one argparse action: its flags and choices are that
        # action's own (a multi-flag option still has exactly the action's choices)
        for o in node["options"]:
            action = p._option_string_actions[o["flags"][0]]
            assert list(o["flags"]) == list(action.option_strings), path
            assert o["choices"] == (
                None if action.choices is None else [str(c) for c in action.choices]
            ), path
        # positionals: the same destinations, with each one's choices, in parser order
        pos = _positionals(p)
        assert [x["dest"] for x in node["positionals"]] == [a.dest for a in pos], path
        for x, a in zip(node["positionals"], pos):
            assert x["choices"] == (
                None if a.choices is None else [str(c) for c in a.choices]
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


def _activation_defined(shell: str, text: str) -> bool:
    """The template really DEFINES the activation function, anchored at the start of a
    line, so an echo or comment that merely mentions the name does not pass."""
    name = f"_ddflow_activate_{shell}"
    header = (
        rf"^function\s+{name}\b"
        if shell == "fish"
        else rf"^(?:function\s+)?{name}\s*\(\s*\)"
    )
    return re.search(header, text, re.MULTILINE) is not None


@pytest.mark.parametrize("shell", scripts.SHELLS)
def test_every_shell_script_parses(shell, tmp_path):
    """Each activation template parses under its own shell -- a pure syntax check, with
    the shell's own rc/global config skipped so a broken user config cannot read as a
    syntax error; an uninstalled shell is a coverage gap (unavailable), never a failure."""
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed: the {shell} template is unexercised")
    flags = {"bash": ["-n"], "zsh": ["-f", "-n"], "fish": ["--no-execute", "--no-config"]}
    env = {**os.environ, "BASH_ENV": "", "ENV": ""}  # bash reads these at startup
    text = scripts.for_shell(shell)
    # a degenerate or placeholder script would still parse clean, so also require the
    # real activation function DEFINITION, anchored at line start -- an echo, comment
    # or mention of the name alone does not pass
    assert _activation_defined(shell, text)
    path = tmp_path / f"ddflow-completion.{shell}"
    path.write_text(text, encoding="utf-8")
    result = subprocess.run([exe, *flags[shell], str(path)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, (shell, result.stderr)
