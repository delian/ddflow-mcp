"""B0546685516: `ddflow tests --item` states which mode the unit_tests gate would use right
now and why, instead of always saying it runs the whole suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_econ_tests import _ci, _commit, _fix, _project


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
