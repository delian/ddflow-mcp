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
    code, _, err = run_cli(repo, "bug", "show", "fix-B1")
    assert code != 0 and "not a bug" in err
    code, _, err = run_cli(repo, "bug", "show", "B-nope")
    assert code != 0 and err


def test_bug_help_lists_show(repo):
    code, out, _ = run_cli(repo, "bug", "--help")
    assert code == 0 and re.search(r"^\s+show\s", out, re.M)


def test_a_task_whose_text_mentions_a_bug_is_still_not_a_bug(repo):
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", "the bug in f returns 1")
    code, _, err = run_cli(repo, "bug", "show", "fix-B2")
    assert code != 0 and "not a bug" in err


def test_show_lists_only_the_real_fix_task_not_a_text_claimer(repo):
    """B0b1f135e37: an item whose TITLE claims to fix a bug but carries no real link (its
    `fixes` is empty, the bug has no `fix_task`) must not be printed as `fix task(s):` --
    `complete <item> --regression-test` checks the link, not the text, and refused it."""
    from ddflow.api import knowledge as K
    from ddflow.infra.log import EventLog

    run_cli(repo, "init")
    EventLog(repo, "seed").append("phase.added", "P1", {"title": "p"})
    EventLog(repo, "seed").append(
        "task.added", "PS-x", {"parent": "P1", "title": "Fixes bug B100", "globs": ["s.py"]}
    )
    K.bug_found(repo, summary="broke", id="B100", no_task=True, agent="a")
    code, out, err = run_cli(repo, "show", "B100")
    assert code == 0, err
    assert "fix task(s): PS-x" not in out, out
    assert "mentioned by: PS-x" in out, out


def test_show_still_lists_the_real_fix_task(repo):
    """The real link still appears once it exists: `bug found` files fix-B3 and `show B3`
    lists it, not a mention."""
    run_cli(repo, "init")
    run_cli(repo, "bug", "found", "--id", "B3", "--summary", "it returns 1")
    code, out, err = run_cli(repo, "show", "B3")
    assert code == 0, err
    assert "fix task(s): fix-B3" in out, out
