"""The documented extra review round is allowed however the command is launched (bug B176b2f67db).

`ddflow review <id> --gate G --force --reason "..."` is the one `--force` the guard lets through
(`[hooks].allowed_commands`). The exception matched only a command whose own word is `ddflow`, so
the same round started through `timeout` or `python -m ddflow` was refused by the plain
`--force` entry -- which read as 'only allowed when --force follows the item id'.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.services import forbidden as FB

ROUND = 'review X --gate rubber_duck --force --reason "an extra round"'


def _check(cmd: str):
    return FB.check(cmd, Config(), env={}, cwd="/nonexistent")


@pytest.mark.parametrize(
    "launcher",
    [
        "ddflow",
        "ddflow --agent a",
        "timeout 580 ddflow",
        "timeout -k 5 580 ddflow --agent a",
        "python -m ddflow",
        "python3 -m ddflow --agent a",
        "/home/me/.venv/bin/python3 -P -m ddflow",
        "KIMI_API_KEY=x ddflow",
        "env KIMI_API_KEY=x timeout 580 ddflow",
        "nohup ddflow",
        "timeout .5s ddflow",
        "python3.13t -m ddflow",
        "python -mddflow",
        "pypy3 -m ddflow",
    ],
)
def test_the_documented_round_is_allowed_however_it_is_launched(launcher):
    assert _check(f"{launcher} {ROUND}") is None, launcher


@pytest.mark.parametrize(
    "cmd",
    [
        "timeout 580 ddflow review X --gate critic --force",  # no --reason
        "python -m ddflow merge X --force",
        "timeout 5 git push --force",
        "python-tools -m ddflow review X --gate critic --force --reason w",  # not an interpreter
        "python - -m ddflow review X --gate critic --force --reason w",  # the script is on stdin
        "python -- -m ddflow review X --gate critic --force --reason w",  # a file named -m
        "python --check-hash-based-pycs always -m ddflow review X --gate critic --force --reason w",
        "timeout 5 5 ddflow review X --gate critic --force --reason w",  # a command named 5
        "python -c 'x' -m ddflow review X --gate critic --force --reason w",
    ],
)
def test_other_force_flags_and_forbidden_commands_stay_refused_when_wrapped(cmd):
    assert _check(cmd) is not None, cmd


def test_a_review_force_without_a_reason_says_what_is_accepted():
    msg = _check("ddflow review X --gate critic --force").message()
    assert "--force --reason" in msg and "anywhere" in msg, msg


@pytest.mark.parametrize(
    "tail",
    [
        'review X --force --reason "r" --gate critic',
        'review X --reason "r" --gate critic --force',
        'review X --gate critic --reason "r" --force',
        'review --gate critic X --force --reason "r"',
        'review X --gate rubber_duck,critic --force --reason "r" --chunk 2',
    ],
)
def test_force_and_reason_may_sit_anywhere_among_the_review_arguments(tail):
    assert _check(f"ddflow {tail}") is None, tail
    assert _check(f"timeout 580 ddflow {tail}") is None, tail


def test_a_python_m_entry_of_the_operator_keeps_matching():
    """`python -m MOD` is MOD for an entry that names MOD and itself for one that names python."""
    words = ["python", "-m", "http.server", "8000"]
    assert FB.matches("python -m http.server", words)
    assert FB.matches("http.server 8000", words)
    assert not FB.matches("python -m other", words)
