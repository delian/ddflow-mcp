"""The regression-test verifier counts only a test that RAN (Bregtest-skip-counted-as-pass).

A test SKIPPED on the pre-fix tree used to read as "PASSES on the pre-fix tree" and refuse a
valid guard; a test that was not collected, errored, or was xfail there read as "fails
before"; and a skip on the fixed tree read as "passes". None of those is evidence: each is
`could-not-run`, naming what happened, never `verified` and never a refusal of the test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_bug_regression_verified import OK, _case, _commit

from ddflow import api

HEAD = (
    "import pathlib, sys\n"
    "import pytest\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
    "from src import f\n\n\n"
)

#: name -> (test source, node id, what the verdict must say)
CASES = {
    "skipped_before": (
        "def test_f():\n    if f() == 1:\n        pytest.skip('old tree')\n    assert f() == 2\n",
        "tests/test_s.py::test_f",
        "skipped",
    ),
    "skipped_on_the_fix": (
        "def test_f():\n    if f() == 2:\n        pytest.skip('fixed tree')\n    assert f() == 2\n",
        "tests/test_s.py::test_f",
        "skipped",
    ),
    "xfail_before": (
        "@pytest.mark.xfail\ndef test_f():\n    assert f() == 2\n",
        "tests/test_s.py::test_f",
        "xfail",
    ),
    "not_collected_before": (
        "if f() == 2:\n    def test_g():\n        assert f() == 2\n",
        "tests/test_s.py::test_g",
        "no test",
    ),
    "error_before": (
        "@pytest.fixture\ndef boom():\n    assert f() == 2\n\n\n"
        "def test_f(boom):\n    assert f() == 2\n",
        "tests/test_s.py::test_f",
        "error",
    ),
}


def _check(repo, name: str):
    source, node, _word = CASES[name]
    wt = _case(repo)
    (wt / "tests" / "test_s.py").write_text(HEAD + source)
    _commit(wt, name)
    return api.bug_fixed(repo, "B1", regression_test=node)


def _assert_gap(repo, name: str) -> None:
    out = _check(repo, name)
    assert out.exit == OK, (name, out.reason)
    assert out.data["regression_verified"] == "could-not-run", (name, out.data)
    ev = out.data.get("regression_verify") or {}
    assert CASES[name][2] in str(ev.get("reason", "")).lower(), (name, ev)


def test_a_test_skipped_on_the_pre_fix_tree_is_not_a_passing_run(repo):
    _assert_gap(repo, "skipped_before")


def test_a_test_skipped_on_the_fixed_tree_does_not_pass(repo):
    _assert_gap(repo, "skipped_on_the_fix")


def test_an_xfail_test_proves_nothing(repo):
    _assert_gap(repo, "xfail_before")


def test_a_test_not_collected_on_the_pre_fix_tree_did_not_fail_before(repo):
    _assert_gap(repo, "not_collected_before")


def test_a_test_that_errors_on_the_pre_fix_tree_did_not_fail_before(repo):
    _assert_gap(repo, "error_before")


def test_a_run_that_ended_without_a_test_failure_did_not_fail_before():
    from ddflow.services.gates import not_a_failure

    for code in (2, 3, 4, 5):  # interrupted/collection error, internal error, not found, none
        assert not_a_failure({"exit": code, "summary": []}, where="the pre-fix tree"), code
    assert not_a_failure({"exit": 1, "summary": ["1 failed in 0.1s"]}, where="x") == ""
    # an interrupted run that had already failed the test still shows it failing
    assert not_a_failure({"exit": 2, "summary": ["1 failed, 1 passed in 1s"]}, where="x") == ""
