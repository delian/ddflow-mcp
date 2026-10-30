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
    assert run_cli(repo, "bug", "invalid", "B1", "--reason", "nope")[0] == 0
    assert not _load(repo)[2].bugs["B1"].open
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


def test_bug_show_carries_tags(repo):
    """`show` of a bug carries its tags (the text line is fix-Bc930562bd1-show-line)."""
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "security")
    code, out, err = run_cli(repo, "--json", "bug", "show", "B1")
    assert code == 0, (out, err)
    assert json.loads(out)["tags"] == ["security"]


def test_get_set_pair_is_the_carrier(repo):
    run_cli(repo, "init")
    _found(repo, "B1")
    assert BT.get_tags(_load(repo)[2], "B1") == []
    assert BT.set_tags(repo, "B1", ["x", "y"]).ok
    assert BT.get_tags(_load(repo)[2], "B1") == ["x", "y"]


def test_old_log_without_tags_replays_unchanged(repo):
    """A `bug.found` written before tags existed (no `tags` key) folds to []."""
    from ddflow.core.model import fold
    from ddflow.infra.log import Event

    run_cli(repo, "init")
    old = Event(
        kind="bug.found",
        subject="Bold",
        data={"item": "", "summary": "old bug"},
        agent="a",
        lamport=1,
        ts="2026-01-01T00:00:01Z",
    )
    old = Event(**{**old.__dict__, "id": old.compute_id()})
    assert fold([old], strict=False).bugs["Bold"].tags == []


def test_derived_wave_tags_are_neither_set_nor_removed_by_hand(repo):
    run_cli(repo, "init")
    _found(repo, "B1")
    log = _load(repo)[0]
    log.append("bug.tagged", "B1", {"tags": ["wave:2", "a"]})  # as `waves --stamp` would
    assert run_cli(repo, "bug", "tag", "B1", "--remove", "wave:2")[0] == 3
    assert BT.set_tags(repo, "B1", ["b"]).ok
    assert _tags(repo, "B1") == ["wave:2", "b"]


def test_set_get_round_trips_and_a_re_report_does_not_undo_a_removal(repo):
    run_cli(repo, "init")
    _found(repo, "B1", "--tags", "security")
    _load(repo)[0].append("bug.tagged", "B1", {"tags": ["wave:2", "security"]})
    held = BT.get_tags(_load(repo)[2], "B1")
    assert held == ["wave:2", "security"]
    assert BT.set_tags(repo, "B1", held).ok
    assert _tags(repo, "B1") == ["wave:2", "security"]
    assert run_cli(repo, "bug", "tag", "B1", "--remove", "security")[0] == 0
    assert _tags(repo, "B1") == ["wave:2"]
    _found(repo, "B1", "--tags", "security")  # an explicit-id re-report
    assert _tags(repo, "B1") == ["wave:2"]
