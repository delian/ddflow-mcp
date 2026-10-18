"""`ddflow update` and `ddflow_update` build their ItemEdit through ONE function, so a
field added to the dataclass cannot be wired into one surface and missed in the other
(DX-update-cli-api-duplicate)."""

from __future__ import annotations

import dataclasses

from ddflow import api
from ddflow.core import outcome as O
from ddflow.surfaces import cli
from ddflow.surfaces.declared.queue import item_edit

SAMPLE = {
    "title": "t",
    "body": "b",
    "needs": ["a"],
    "add_needs": ["b"],
    "remove_needs": ["c"],
    "add_tags": ["x"],
    "remove_tags": ["y"],
    "globs": ["g/**"],
    "add_globs": ["h/**"],
    "remove_globs": ["i/**"],
    "tags": ["z"],
    "priority": 7,
    "line": "v2",
    "resources": ["gpu:1"],
    "worktree": "/w",
    "queue": True,
}


def test_every_item_edit_field_is_encoded():
    fields = {f.name for f in dataclasses.fields(api.ItemEdit)}
    assert fields == set(SAMPLE), "add the new ItemEdit field to item_edit and to SAMPLE"
    edit = item_edit(SAMPLE)
    defaults = api.ItemEdit()
    for f in dataclasses.fields(api.ItemEdit):
        assert getattr(edit, f.name) != getattr(defaults, f.name), f"item_edit drops {f.name}"


def test_cli_and_declared_paths_build_the_same_edit(monkeypatch, tmp_path):
    seen = []

    def fake(repo, item, edit, *, agent=""):
        seen.append(edit)
        return O.refused("test", "stop here")

    monkeypatch.setattr(cli.A_ITEMS, "update", fake)
    monkeypatch.chdir(tmp_path)
    argv = [
        "update", "T1", "--title", "t", "--body", "b", "--needs", "a",
        "--add-needs", "b", "--remove-needs", "c", "--add-tags", "x",
        "--remove-tags", "y", "--globs", "g/**", "--add-globs", "h/**",
        "--remove-globs", "i/**", "--tags", "z", "--priority", "7",
        "--line", "v2", "--resources", "gpu:1", "--worktree", "/w", "--queue",
    ]  # fmt: skip
    cli.main(argv)
    assert seen, "the CLI never reached the api"
    assert seen[0] == item_edit(SAMPLE)


def test_declared_call_builds_the_same_edit(monkeypatch):
    from ddflow.surfaces.declared.queue import COMMANDS
    from ddflow.surfaces.registry import by_tool

    seen = []

    def fake(repo, item, edit, *, agent=""):
        seen.append(edit)
        return O.refused("test", "stop here")

    monkeypatch.setattr(api, "update", fake)
    by_tool(COMMANDS)["ddflow_update"].call(None, {"id": "T1", **SAMPLE}, "a")
    assert seen == [item_edit(SAMPLE)]


def test_item_edit_splits_comma_lists():
    assert item_edit({"needs": "a,b"}).needs == ["a", "b"]
