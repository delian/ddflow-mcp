"""`bug fixed` names why the regression check could not run (bug Bf144280340)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli
from test_bug_regression_no_task import ALWAYS_PASSES
from test_bug_regression_verified import _commit

TEST = "tests/test_x.py::test_always"


def _unrun(repo):
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(ALWAYS_PASSES)
    _commit(repo, "a test")
    run_cli(repo, "task", "add", "T2", "--title", "elsewhere", "--globs", "x.py")
    run_cli(repo, "bug", "found", "--id", "B2", "--item", "T2", "--no-task", "--summary", "s")


def test_the_cli_says_why_the_check_could_not_run_and_what_would_let_it(repo):
    _unrun(repo)
    code, out, err = run_cli(repo, "bug", "fixed", "B2", "--regression-test", TEST)
    assert code == 0, err
    assert "[could-not-run]" in out
    assert "not working in a worktree" in out and "to fix:" in out, out


def test_the_json_payload_carries_the_reason(repo):
    _unrun(repo)
    code, out, err = run_cli(repo, "--json", "bug", "fixed", "B2", "--regression-test", TEST)
    assert code == 0, err
    assert "not working in a worktree" in out, out


def test_a_could_not_run_without_a_reason_still_gets_one():
    from ddflow.api.knowledge.bug_close import _regression_status
    from ddflow.services import gates as G

    status, ev = _regression_status(G.REGRESSION_COULD_NOT_RUN, {}, G)
    assert status == "could-not-run" and ev["reason"] and ev["remedy"]


def test_an_unrecognized_status_also_names_a_remedy():
    from ddflow.api.knowledge.bug_close import _regression_status, could_not_run_text
    from ddflow.services import gates as G

    status, ev = _regression_status("brand-new", {}, G)
    assert status == "could-not-run" and "to fix:" in could_not_run_text(ev)
