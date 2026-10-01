"""Global options work after the subcommand too (B9811d8617c).

`--agent`, `--repo` and `--json` existed only on the root parser, so
`ddflow brief --agent impl-1 --item T1` failed "unrecognized arguments: --agent" while
`ddflow --agent impl-1 brief --item T1` worked. Agents append flags, and every driver
says "pass --agent on every call" without saying where.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.cli import build_parser


@pytest.mark.parametrize(
    "argv",
    [
        ["brief", "--agent", "impl-1", "--item", "T1"],
        ["brief", "--item", "T1", "--agent=impl-1"],
        ["gate", "run", "T1", "unit_tests", "--agent", "impl-1"],
        ["bug", "found", "--summary", "s", "--agent", "impl-1"],
    ],
)
def test_agent_after_the_subcommand(argv):
    assert build_parser().parse_args(argv).agent == "impl-1"


def test_repo_and_json_after_the_subcommand():
    a = build_parser().parse_args(["status", "--repo", "/x", "--json"])
    assert a.repo == "/x" and a.json is True


def test_a_value_before_the_subcommand_is_not_reset_by_its_absence_after():
    a = build_parser().parse_args(["--agent", "lead", "--json", "--repo", "/x", "status"])
    assert (a.agent, a.json, a.repo) == ("lead", True, "/x")


def test_after_wins_over_before_when_both_are_given():
    a = build_parser().parse_args(["--agent", "lead", "status", "--agent", "impl-1"])
    assert a.agent == "impl-1"


def test_no_global_options_keeps_the_defaults():
    a = build_parser().parse_args(["status"])
    assert (a.agent, a.json, a.repo) == (None, False, None)


def test_end_to_end_the_event_carries_the_trailing_agent(repo):
    """Through the real CLI: the identity given after the subcommand writes the event."""
    assert run_cli(repo, "init")[0] == 0
    code, out, err = run_cli(repo, "task", "add", "T1", "--globs", "a.py", "--agent", "impl-1")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "show", "T1", "--json")
    assert code == 0, out + err
    json.loads(out)  # --json after the subcommand took effect
    shards = {p.stem for p in (repo / ".ddflow" / "events").glob("*.jsonl")}
    assert "impl-1" in shards, shards
