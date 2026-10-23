"""Bee3becc481: a CI-filed lint bug (`ruff format --check` failed) has no pytest to name, so
`complete`/`bug fixed` could not close it short of an operator gate skip. `--lint-exempt REASON`
closes it with the reason recorded; any other bug still needs its regression test."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def _file(repo: Path, bug: str, title: str) -> None:
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "bug", "found", "--id", bug, "--summary", "red", "--title", title)
    assert code == 0, err


def test_a_ci_lint_bug_closes_with_a_recorded_reason_and_no_test(repo):
    _file(repo, "Bci-ruff-format-1", "merge CI check failed: ruff format --check (CI: Lint)")
    out = api.bug_fixed(repo, "Bci-ruff-format-1", lint_exempt="ruff format applied; check green")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "exempt-lint"
    bug = fold(EventLog(repo).read_all()).bugs["Bci-ruff-format-1"]
    assert bug.fixed_at and not bug.regression_tests
    (fixed,) = [e for e in EventLog(repo).read_all() if e.kind == "bug.fixed"]
    assert fixed.data["regression_verify"]["reason"] == "ruff format applied; check green"
    assert fixed.data["regression_verify"]["lint_check"] == "ruff format --check (CI: Lint)"


def test_a_bug_that_is_not_a_ci_lint_check_still_needs_its_test(repo):
    _file(repo, "B1", "an ordinary bug")
    out = api.bug_fixed(repo, "B1", lint_exempt="no test, honest")
    assert out.exit == REFUSED and "CI-filed lint bug" in out.reason, out.reason
    _file2 = run_cli(repo, "bug", "found", "--id", "Bci-t", "--summary", "r", "--title",
                     "pre-push CI check failed: tests/test_a.py::test_x")  # fmt: skip
    assert _file2[0] == 0
    out = api.bug_fixed(repo, "Bci-t", lint_exempt="x")
    assert out.exit == REFUSED, "a failing TEST is not a lint check"
    assert not fold(EventLog(repo).read_all()).bugs["Bci-t"].fixed_at


def test_the_reason_is_required_and_excludes_a_test(repo):
    _file(repo, "Bci-ruff-format-2", "merge CI check failed: ruff format --check (CI: Lint)")
    # (an empty reason is the ordinary path: it asks for the test)
    out = api.bug_fixed(repo, "Bci-ruff-format-2", lint_exempt="   ")
    assert out.exit == REFUSED and "reason" in out.reason, out.reason
    both = api.bug_fixed(repo, "Bci-ruff-format-2", lint_exempt="r", regression_test="t.py::t")
    assert both.exit == REFUSED and "alternatives" in both.reason


def test_the_cli_takes_lint_exempt(repo):
    _file(repo, "Bci-ruff-format-3", "merge CI check failed: ruff format --check (CI: Lint)")
    code, out, err = run_cli(
        repo, "bug", "fixed", "Bci-ruff-format-3", "--lint-exempt", "ruff format applied"
    )
    assert code == OK, err
    assert "exempt-lint" in out or "closed" in out


def test_a_security_or_other_non_lint_ci_check_still_needs_its_test(repo):
    _file(repo, "Bci-bandit-1", "merge CI check failed: bandit (CI: Static security scan)")
    out = api.bug_fixed(repo, "Bci-bandit-1", lint_exempt="no test")
    assert out.exit == REFUSED and "CI-filed lint bug" in out.reason, out.reason


def test_a_non_pytest_check_id_containing_colons_is_not_taken_for_a_test(repo):
    _file(repo, "Bci-ruff-colons", "merge CI check failed: ruff::format (CI: Lint)")
    assert api.bug_fixed(repo, "Bci-ruff-colons", lint_exempt="formatted").exit == OK
