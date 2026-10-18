"""B55b2003e9b: `ddflow bug show <id>` reads a bug, through the same machinery as `ddflow show`."""

from __future__ import annotations

import json
import re

from conftest import run_cli


def _found(repo, bug="B1"):
    code, _, err = run_cli(repo, "init")
    assert code == 0, err
    code, _, err = run_cli(repo, "bug", "found", "--id", bug, "--summary", "f returns 1")
    assert code == 0, err


def test_bug_show_prints_what_show_prints_for_a_bug(repo):
    _found(repo)
    code, out, err = run_cli(repo, "bug", "show", "B1")
    assert code == 0, err
    assert run_cli(repo, "show", "B1")[1] == out
    assert "B1 [bug]" in out and "f returns 1" in out


def test_bug_show_json_is_the_show_json(repo):
    _found(repo)
    code, out, _ = run_cli(repo, "--json", "bug", "show", "B1")
    assert code == 0
    shown = json.loads(run_cli(repo, "--json", "show", "B1")[1])
    got = json.loads(out)
    assert {**got, "schema": ""} == {**shown, "schema": ""} and got["item"] == shown["item"]


def test_bug_show_refuses_what_is_not_a_bug(repo):
    _found(repo)
    code, out, err = run_cli(repo, "bug", "show", "fix-B1")
    assert code != 0 and "not a bug" in err
    code, _, err = run_cli(repo, "bug", "show", "B-nope")
    assert code != 0 and err


def test_bug_help_lists_show(repo):
    code, out, _ = run_cli(repo, "bug", "--help")
    assert code == 0 and re.search(r"^\s+show\s", out, re.M)
