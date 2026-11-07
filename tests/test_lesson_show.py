"""`ddflow lesson show <id>` reads one lesson in full, by id (bug B6bf97a62e0).

Before this, a lesson's id was cited by `recall`, `brief` and link text, but `ddflow
show <id>` answered "no such item or bug", `lesson` had no `show` verb, and `lesson
search <id>` returned nothing — a lesson's rule/why/how was reachable only by guessing
words from its title.
"""

from __future__ import annotations

import json

from conftest import run_cli


def _lesson(repo, lid, title, *, rule="", why="", how=""):
    args = ["lesson", "add", "--id", lid, "--title", title, "--summary", title]
    if rule:
        args += ["--rule", rule]
    if why:
        args += ["--why", why]
    if how:
        args += ["--how", how]
    code, _, err = run_cli(repo, *args)
    assert code == 0, err


def test_lesson_show_reads_one_lesson_in_full_by_id(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "one id, one meaning", rule="resolve an id once", why="drift", how="grep first")
    code, out, err = run_cli(repo, "lesson", "show", "L1")
    assert code == 0, err
    assert "L1" in out and "one id, one meaning" in out
    assert "resolve an id once" in out and "drift" in out and "grep first" in out


def test_lesson_show_json_carries_the_lesson_fields(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "a rule", rule="full rule text")
    code, out, _ = run_cli(repo, "--json", "lesson", "show", "L1")
    assert code == 0, out
    body = json.loads(out)
    assert body["id"] == "L1" and body["title"] == "a rule"
    assert body["rule"] == "full rule text"


def test_lesson_show_refuses_an_unknown_id(repo):
    run_cli(repo, "init")
    code, _, err = run_cli(repo, "lesson", "show", "L-nope")
    assert code != 0 and "no such lesson" in err


def test_lesson_help_lists_show(repo):
    code, out, _ = run_cli(repo, "lesson", "--help")
    assert code == 0 and "show" in out
