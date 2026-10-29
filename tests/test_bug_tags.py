"""Tags on bug records (Bc930562bd1): the bug carrier the label engine will wrap.

`bug found --tags`, `bug tag BUG --add/--remove` on an open or a closed bug, `list bug
--tag` / `bug list --tag`, tags in `bug show`, and a fold that leaves old logs unchanged.
`wave:*` stays reserved: it is derived, never set by hand (D-labels).
"""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.api import _load
from ddflow.api.knowledge import bug_tags as BT


def _found(repo, bid, *extra):
    code, out, err = run_cli(
        repo, "bug", "found", "--id", bid, "--summary", f"{bid} broken", "--no-task", *extra
    )
    assert code == 0, (out, err)


def _tags(repo, bid):
    return list(_load(repo)[2].bugs[bid].tags)


def test_found_with_tags_records_them(repo):
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "security,perf")
    _found(repo, "B2")
    assert _tags(repo, "B1") == ["security", "perf"]
    assert _tags(repo, "B2") == []


def test_tag_adds_and_removes_on_open_and_closed_bug(repo):
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "a")
    code, out, err = run_cli(repo, "bug", "tag", "B1", "--add", "b,ns:c", "--remove", "a")
    assert code == 0, (out, err)
    assert _tags(repo, "B1") == ["b", "ns:c"]
    run_cli(repo, "bug", "invalid", "B1", "--reason", "nope")
    code, out, err = run_cli(repo, "bug", "tag", "B1", "--add", "late")
    assert code == 0, (out, err)
    assert _tags(repo, "B1") == ["b", "ns:c", "late"]


def test_tag_refusals(repo):
    run_cli(repo, "init")
    _found(repo, "B1")
    assert run_cli(repo, "bug", "tag", "NOPE", "--add", "x")[0] == 3
    assert run_cli(repo, "bug", "tag", "B1", "--add", "wave:3")[0] == 3
    assert run_cli(repo, "bug", "tag", "B1")[0] != 0
    assert _tags(repo, "B1") == []


def test_list_filters_by_tag_on_both_forms(repo):
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "security")
    _found(repo, "B2", "--tags", "perf")
    for argv in (("list", "bug", "--tag", "security"), ("bug", "list", "--tag", "security")):
        code, out, err = run_cli(repo, *argv, "--json")
        assert code == 0, (out, err)
        body = json.loads(out)
        assert [r["id"] for r in body["rows"]] == ["B1"]
        assert body["rows"][0]["tags"] == ["security"]


def test_bug_show_prints_tags(repo):
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "security")
    code, out, err = run_cli(repo, "bug", "show", "B1")
    assert code == 0, (out, err)
    assert "security" in out


def test_get_set_pair_is_the_carrier(repo):
    run_cli(repo, "init")
    _found(repo, "B1")
    assert BT.get_tags(_load(repo)[2], "B1") == []
    assert BT.set_tags(repo, "B1", ["x", "y"]).ok
    assert BT.get_tags(_load(repo)[2], "B1") == ["x", "y"]


def test_old_log_without_tags_replays_unchanged(repo):
    run_cli(repo, "init")
    _found(repo, "B1")
    assert _tags(repo, "B1") == []
