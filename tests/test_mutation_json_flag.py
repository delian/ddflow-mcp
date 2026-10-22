"""B-complete-mutation-flag-ambiguous: `--mutation '<json {file,old,new}>'` on the CLI, as MCP.

`--mutation` used to be an ambiguous abbreviation of --mutation-file/-old/-new and argparse
refused it. It is now the JSON flag itself (parity with the MCP `mutation` parameter); the
three flags stay.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.declared.mutation_params import mutation_of

JSON = json.dumps({"file": "t.py", "old": "assert x", "new": "pass"})
WANT = {"file": "t.py", "old": "assert x", "new": "pass"}


def test_complete_accepts_mutation_json_without_ambiguity(repo):
    run_cli(repo, "init")
    for argv in (
        ("complete", "nope", "--mutation", JSON),
        ("bug", "fixed", "Bnope", "--mutation", JSON),
    ):
        code, _out, err = run_cli(repo, *argv)
        assert "ambiguous" not in err and "unrecognized" not in err, err
        assert code != 0  # no such item/bug: it got past argument parsing


def test_the_json_flag_and_the_three_flags_read_back_the_same():
    assert mutation_of({"mutation": JSON}) == WANT
    assert (
        mutation_of({"mutation_file": "t.py", "mutation_old": "assert x", "mutation_new": "pass"})
        == WANT
    )


def test_the_cli_namespace_carries_the_json(repo):
    from ddflow.surfaces.cli import build_parser

    ns = build_parser().parse_args(["complete", "x", "--mutation", JSON])
    assert mutation_of(vars(ns)) == WANT
