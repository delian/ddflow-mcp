"""Bug B-dashdash-312: `ddflow task -- list` died with "invalid choice: '--'" on Python
3.12.3. Before 3.13 argparse leaves the `--` at the head of a subparsers action's values
(it strips it only for ordinary positionals), so the command word check refused `--` itself.
`SuggestingParser._get_values` now drops that one marker where argparse has not."""

from __future__ import annotations

import argparse
import contextlib
import io

import pytest

from ddflow.surfaces import registry as R
from ddflow.surfaces.cli import build_parser


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _plain_accepts(*argv: str) -> bool:
    """Does THIS Python's own argparse accept the vector on a two-level command line?"""
    top = argparse.ArgumentParser(exit_on_error=False)
    a = top.add_subparsers(dest="a").add_parser("task")
    a.add_subparsers(dest="b").add_parser("list")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            top.parse_args(list(argv))
    except (argparse.ArgumentError, SystemExit):
        return False
    return True


def _ddflow_accepts(*argv: str) -> bool:
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args(list(argv))
    except SystemExit:
        return False
    return True


def test_where_argparse_leaves_the_marker_the_head_values_lose_it(monkeypatch):
    """Forces the pre-3.13 path, so it fails on any Python without the fix."""
    monkeypatch.setattr(R, "_STDLIB_DROPS_SUBCOMMAND_DASHDASH", False)
    parser = build_parser()
    sub = _subparsers_action(parser)
    assert parser._get_values(sub, ["--", "brief", "x"]) == ["brief", "x"]
    assert parser._get_values(sub, ["brief", "--", "x"]) == ["brief", "--", "x"]
    # a lone marker reaches the command word check untouched (it is refused there)
    with pytest.raises(argparse.ArgumentError):
        parser._get_values(sub, ["--"])


def test_where_argparse_already_drops_it_the_values_are_not_stripped_twice(monkeypatch):
    monkeypatch.setattr(R, "_STDLIB_DROPS_SUBCOMMAND_DASHDASH", True)
    parser = build_parser()
    sub = _subparsers_action(parser)
    # a literal `--` after the marker argparse took is the user's, and an error as before
    with pytest.raises(argparse.ArgumentError):
        parser._get_values(sub, ["--", "brief"])


def test_ddflow_accepts_a_double_dash_before_the_command_word_on_every_python():
    parser = build_parser()
    assert R.parsed_path(parser.parse_args(["task", "--", "list"])) == ("task", "list")
    assert R.parsed_path(parser.parse_args(["--", "brief"])) == ("brief",)


def test_ddflow_agrees_with_this_pythons_argparse_about_a_second_marker():
    """`task -- -- list`: the second `--` is a literal word. Whatever this Python's own
    argparse makes of it, ddflow's parser makes the same (no double strip on 3.13)."""
    assert _ddflow_accepts("task", "--", "--", "list") == _plain_accepts("task", "--", "--", "list")


def test_a_double_dash_alone_is_still_an_error():
    assert not _ddflow_accepts("task", "--")
