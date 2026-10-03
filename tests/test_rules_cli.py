"""`ddflow rule ...`: the CLI face of the project-rules API, end to end."""

from __future__ import annotations

import json

from conftest import run_cli


def _add(repo, rid, title, content, *extra):
    return run_cli(repo, "rule", "add", "--id", rid, "--title", title, "--content", content, *extra)


def test_add_list_show_edit_search_remove_roundtrip(repo):
    code, out, err = _add(repo, "r-naming", "Naming", "snake_case for functions", "--tags", "style")
    assert code == 0, out + err
    code, out, _ = run_cli(repo, "--json", "rule", "list", "--tag", "style")
    assert (
        [r["id"] for r in json.loads(out)["rows"]] == ["r-naming"]
        if isinstance(json.loads(out), dict)
        else [r["id"] for r in json.loads(out)] == ["r-naming"]
    )
    code, out, _ = run_cli(repo, "rule", "show", "r-naming")
    assert code == 0 and "snake_case for functions" in out
    code, out, err = run_cli(repo, "rule", "edit", "r-naming", "--priority", "80")
    assert code == 0, out + err
    code, out, _ = run_cli(repo, "--json", "rule", "show", "r-naming")
    assert json.loads(out)["priority"] == 80
    code, out, _ = run_cli(repo, "rule", "search", "snake_case")
    assert code == 0 and "r-naming" in out
    code, out, err = run_cli(repo, "rule", "remove", "r-naming")
    assert code == 0, out + err
    code, _, _ = run_cli(repo, "rule", "show", "r-naming")
    assert code != 0


def test_a_duplicate_is_refused_until_answered(repo):
    _add(repo, "r-a", "Tests first", "write the failing test before the fix")
    code, out, err = _add(repo, "r-b", "Tests first again", "write the failing test before the fix")
    assert code == 3, out + err
    code, out, err = _add(
        repo, "r-b", "Tests first again", "write the failing test before the fix", "--new"
    )
    assert code == 0, out + err


def test_json_refusal_carries_the_candidates(repo):
    _add(repo, "r-a", "Tests first", "write the failing test before the fix")
    code, out, _ = run_cli(
        repo, "--json", "rule", "add", "--id", "r-b", "--title", "Again",
        "--content", "write the failing test before the fix",
    )  # fmt: skip
    assert code == 3
    assert [c["id"] for c in json.loads(out)["candidates"]] == ["r-a"]
