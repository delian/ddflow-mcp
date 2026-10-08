"""B0546685516: `ddflow tests --item` states which mode the unit_tests gate would use right
now and why, instead of always saying it runs the whole suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_econ_tests import _ci, _cli, _commit, _fix, _project, _task, _worktree


def _tests(repo, item: str) -> tuple[dict, str]:
    _code, out, _err = run_cli(repo, "--json", "tests", "--item", item)
    _code2, text, _e2 = run_cli(repo, "tests", "--item", item)
    return json.loads(out), text


def test_selected_when_ci_passed_on_this_tree(repo):
    _project(repo)
    _fix(repo)
    _ci(repo, "fix-B1")
    body, text = _tests(repo, "fix-B1")
    gate = (body.get("data") or body)["unit_tests_gate"]
    assert gate["scope"] == "selected", gate
    assert "gate runs only the selected tests" in text
    assert gate["tests"] and "tests/test_f.py  --" in text.split("gate runs only")[1]
    assert "still runs the whole suite" not in text


def test_whole_suite_without_ci_says_why(repo):
    _project(repo)
    _fix(repo)
    body, text = _tests(repo, "fix-B1")
    gate = (body.get("data") or body)["unit_tests_gate"]
    assert gate["scope"] == "full" and "ci gate has no outcome" in gate["why"], gate
    assert "runs the whole suite" in text and "ci gate has no outcome" in text


def test_whole_suite_after_an_edit_says_the_tree_changed(repo):
    _project(repo)
    wt = _fix(repo)
    _ci(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2  # edit\n")
    _commit(wt, "later commit")
    _body, text = _tests(repo, "fix-B1")
    assert "the tree changed since the ci gate passed" in text


def _gate(repo, item: str) -> dict:
    body, _text = _tests(repo, item)
    return (body.get("data") or body)["unit_tests_gate"]


def test_ci_skipped_and_failed_each_say_so(repo):
    _project(repo)
    _fix(repo)
    _ci(repo, "fix-B1", "skipped")
    assert "ci gate is skipped" in _gate(repo, "fix-B1")["why"]
    _ci(repo, "fix-B1", "failed")
    assert "ci gate is failed" in _gate(repo, "fix-B1")["why"]


def test_a_larger_task_says_so(repo):
    _project(repo)
    _cli(repo, "config", "--set", "gates.unit_tests_small_lines", "20")
    _task(repo, "T1", lines=40)
    _ci(repo, "T1")
    g = _gate(repo, "T1")
    assert g["scope"] == "full" and "(small is under 20)" in g["why"], g


def test_a_change_no_test_reaches_says_so(repo):
    _project(repo)
    _cli(repo, "bug", "found", "--id", "B1", "--summary", "docs are wrong")
    _cli(repo, "claim", "fix-B1")
    wt = _worktree(repo, "fix-B1")
    (wt / "NOTES.txt").write_text("fixed\n")
    _commit(wt, "a change no test reaches")
    _ci(repo, "fix-B1")
    g = _gate(repo, "fix-B1")
    assert g["scope"] == "full" and "no test reaches" in g["why"], g


def test_uncommitted_edits_after_ci_say_the_tree_changed(repo):
    _project(repo)
    wt = _fix(repo)
    _ci(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2  # dirty\n")
    g = _gate(repo, "fix-B1")
    assert g["scope"] == "full" and "the tree changed since the ci gate passed" in g["why"], g
