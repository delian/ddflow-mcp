"""The registry features the hand-written leaves needed (B-uc-surf-registry): a flag with an
optional value, a required mutually exclusive group, a flag that stands alone."""

from __future__ import annotations

import argparse

import pytest

from ddflow.surfaces.registry import Command, Param, add_commands


def _parser(*params: Param) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="t", exit_on_error=False)
    add_commands(root.add_subparsers(dest="cmd"), [Command(path=("go",), params=params)])
    return root


def _optional_value() -> argparse.ArgumentParser:
    return _parser(
        Param("apply", nargs="?", const="all", metavar="CATEGORIES"),
        Param("restore", nargs="?", const="latest", metavar="NAME"),
    )


def test_a_flag_with_an_optional_value_takes_const_alone_a_value_or_nothing():
    p = _optional_value()
    assert p.parse_args(["go"]).apply is None
    assert p.parse_args(["go", "--apply"]).apply == "all"
    assert p.parse_args(["go", "--apply", "repairs,hooks"]).apply == "repairs,hooks"
    assert p.parse_args(["go", "--restore"]).restore == "latest"
    assert p.parse_args(["go", "--restore", "x"]).restore == "x"


def test_an_optional_value_flag_is_not_required_and_shows_its_metavar():
    assert not Param("apply", nargs="?", const="all").mcp_required
    helps = _optional_value()._subparsers._group_actions[0].choices["go"].format_help()
    assert "--apply [CATEGORIES]" in helps


def test_a_required_exclusive_group_wants_exactly_one_member():
    p = _parser(
        Param("a", exclusive="which", exclusive_required=True),
        Param("b", exclusive="which"),
    )
    assert p.parse_args(["go", "--a", "1"]).a == "1"
    assert p.parse_args(["go", "--b", "2"]).b == "2"
    for argv in (["go"], ["go", "--a", "1", "--b", "2"]):
        with pytest.raises((SystemExit, argparse.ArgumentError)):
            p.parse_args(argv)


def test_an_exclusive_group_is_optional_unless_one_member_says_so():
    p = _parser(Param("a", exclusive="which"), Param("b", exclusive="which"))
    assert p.parse_args(["go"]).a is None


def test_misdeclared_features_are_refused():
    with pytest.raises(ValueError, match="nargs='\\?'"):
        Param("x", nargs="*")
    with pytest.raises(ValueError, match="const"):
        Param("x", const="c")
    with pytest.raises(ValueError, match="const"):
        Param("x", positional=True, nargs="?", const="c")
    with pytest.raises(ValueError, match="exclusive_required"):
        Param("x", exclusive_required=True)
