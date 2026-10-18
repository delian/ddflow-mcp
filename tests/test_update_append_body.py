"""`update --append-body` adds to an item's body without replacing it (DX-body-append)."""

from __future__ import annotations

from conftest import run_cli
from helpers import items_of as _items


def _task(repo, body="original"):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "T", "--globs", "t.py", "--body", body)[0] == 0


def test_append_keeps_the_original(repo):
    _task(repo)
    assert run_cli(repo, "update", "T", "--append-body", "a note")[0] == 0
    assert _items(repo)["T"].body == "original\n\na note"


def test_append_twice_accumulates(repo):
    _task(repo)
    run_cli(repo, "update", "T", "--append-body", "one")
    run_cli(repo, "update", "T", "--append-body", "two")
    assert _items(repo)["T"].body == "original\n\none\n\ntwo"


def test_append_to_an_empty_body_has_no_leading_blank_lines(repo):
    _task(repo, body="")
    assert run_cli(repo, "update", "T", "--append-body", "note")[0] == 0
    assert _items(repo)["T"].body == "note"


def test_append_with_body_goes_after_the_new_body(repo):
    _task(repo)
    assert run_cli(repo, "update", "T", "--body", "new", "--append-body", "n")[0] == 0
    assert _items(repo)["T"].body == "new\n\nn"
