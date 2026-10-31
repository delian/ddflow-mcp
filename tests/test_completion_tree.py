"""The completion tree is derived from the real parser: compared with argparse's own
actions at every command path (nothing missing, nothing extra); hidden and compat names
are not offered; the walk is stable, JSON-able and import-light."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

from ddflow.services.completion import tree as T
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


def test_every_path_matches_argparse(parser, tree):
    paths = dict(_parsers(parser))
    walked = dict(T.walk(tree))
    assert set(walked) == set(paths)  # no command missing, none extra
    assert len(paths) > 50
    for path, p in paths.items():
        node = walked[path]
        flags = {f for n in node["options"] for f in n["flags"]}
        assert [list(n["flags"]) for n in node["options"]] == [
            list(a.option_strings)
            for a in p._actions
            if a.option_strings and (a.help != argparse.SUPPRESS or _mirrors(a, parser))
        ], path
        # every offered flag is an option string argparse itself holds, and vice versa
        assert flags <= set(p._option_string_actions), path
        pos = [a for a in p._actions if not a.option_strings and a.nargs != argparse.PARSER]
        assert [x["dest"] for x in node["positionals"]] == [a.dest for a in pos], path
        for opt, act in zip(
            node["options"],
            [a for a in p._actions if a.option_strings and set(a.option_strings) & flags],
            strict=True,
        ):
            assert opt["choices"] == (None if act.choices is None else list(map(str, act.choices)))
            assert opt["arity"] == ("0" if act.nargs == 0 else str(act.nargs or 1))


def _mirrors(action, root):
    return bool(set(action.option_strings) & {s for a in root._actions for s in a.option_strings})


def _compat_parser() -> argparse.ArgumentParser:
    root = SuggestingParser(prog="x")
    add_commands(
        root.add_subparsers(dest="cmd", required=True),
        [
            Command(
                path=("show",),
                summary="show it",
                params=(
                    Param("item", positional=True),
                    Param("note", aliases=("comment",), deprecated_since="0.1.17"),
                ),
                handler=lambda a, c: 0,
                aliases=("display",),
                deprecated_since="0.1.17",
            )
        ],
    )
    return root


def test_compat_flags_and_command_aliases_are_not_offered():
    p = _compat_parser()
    got = T.command_tree(p)
    sub = p._actions[-1]
    assert "display" in sub.choices and "--comment" in sub.choices["show"]._option_string_actions
    assert list(got["commands"]) == ["show"]  # the command alias answers, is not offered
    flags = [f for o in got["commands"]["show"]["options"] for f in o["flags"]]
    assert "--note" in flags and "--comment" not in flags


def test_real_parser_compat_names_are_not_offered(parser, tree):
    for path, p in _parsers(parser):
        node = T.at(tree, path)
        offered = {f for o in node["options"] for f in o["flags"]}
        assert not set(getattr(p, "_compat_flags", {})) & offered, path
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                assert not set(getattr(a.choices, "hidden", {})) & set(node["commands"]), path
            elif a.help == argparse.SUPPRESS and a.option_strings and not _mirrors(a, parser):
                assert not set(a.option_strings) & offered, (path, a.option_strings)


def test_global_options_are_offered_after_a_subcommand(tree):
    top = {f for o in tree["options"] for f in o["flags"]}
    assert {"--json", "--agent"} <= top
    sub = T.at(tree, ("gate", "record"))
    assert {"--json", "--agent"} <= {f for o in sub["options"] for f in o["flags"]}


def test_stable_and_json_able(parser, tree):
    again = T.command_tree(build_parser())
    assert json.dumps(tree) == json.dumps(again)
    assert json.loads(json.dumps(tree)) == tree
    assert tree["schema"] == T.SCHEMA


def test_walker_is_generic():
    p = argparse.ArgumentParser(prog="x")
    s = p.add_subparsers(dest="c")
    a = s.add_parser("alpha", help="the  first\nword")
    a.add_argument("--mode", choices=["a", "b"])
    a.add_argument("--hidden", help=argparse.SUPPRESS)
    a.add_argument("--many", action="append")
    a.add_argument("name", nargs="?")
    got = T.command_tree(p)
    node = T.at(got, ("alpha",))
    assert node["help"] == "the first word"
    by = {o["flags"][0]: o for o in node["options"]}
    assert "--hidden" not in by
    assert by["--mode"]["choices"] == ["a", "b"] and by["--many"]["repeat"] is True
    assert node["positionals"][0]["arity"] == "?"
    assert T.at(got, ("nope",)) is None


def test_tree_module_is_import_light():
    code = (
        "import sys, ddflow.services.completion.tree as t;"
        "bad=[m for m in sys.modules if m.startswith('ddflow.') and m not in "
        "('ddflow.services.completion','ddflow.services.completion.tree','ddflow.services',"
        "'ddflow')];"
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
