"""Bug B-dashdash-312: `ddflow task -- list` died with "invalid choice: '--'" on Python
3.12.3. Before 3.13 argparse leaves the `--` at the head of a subparsers action's values
(it strips it only for ordinary positionals), so the action took `--` for the command word.
`_WordsRecorder` now drops that one marker, which is what 3.13 does for us."""

from __future__ import annotations

import argparse

import pytest

from ddflow.surfaces import registry as R
from ddflow.surfaces.cli import build_parser


def test_get_values_drops_the_marker_that_opens_a_subcommands_values():
    """Asks the parser what a pre-3.13 argparse would hand a subparsers action, so it fails
    on any Python."""
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert parser._get_values(sub, ["--", "brief", "x"]) == ["brief", "x"]
    assert parser._get_values(sub, ["brief", "--", "x"]) == ["brief", "--", "x"]


def test_the_real_parser_accepts_double_dash_before_the_command_word():
    parser = build_parser()
    assert R.parsed_path(parser.parse_args(["task", "--", "list"])) == ("task", "list")
    assert R.parsed_path(parser.parse_args(["--", "brief"])) == ("brief",)


def test_a_double_dash_alone_is_still_an_error():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["task", "--"])
