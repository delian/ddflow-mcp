"""A bug whose fix lives in the TEST file is verifiable (bug Bc262739f62).

The check overlays the new test file on the base and expects it to fail there; when the
fix IS the test file the overlay carries the fix and `bug fixed` refused every such close.
Now: the base's own test files are tried (meaningful when the test exists there), then a
mutation the agent supplies, which ddflow applies and the test must fail on.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import _commit, _fixed_events, _worktree

from ddflow import api

OK, REFUSED = 0, 3

#: Fails on main: the fixture value is wrong. The fix corrects it in the same file.
BROKEN = "VALUE = 1\n\n\ndef test_v():\n    assert VALUE == 2\n"
FIXED = "VALUE = 2\n\n\ndef test_v():\n    assert VALUE == 2\n"

#: Passes on main (a flake that did not show): the fix adds a guard the test depends on.
FLAKY_BASE = "def test_h():\n    assert True\n"
GUARDED = "GUARD = True\n\n\ndef test_h():\n    assert GUARD\n"


def _repo(repo: Path, base_test: str, name: str) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    (repo / "tests").mkdir()
    (repo / "tests" / name).write_text(base_test)
    _commit(repo, "the test as it was")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "the test's own fixture is wrong")
    code, _out, err = run_cli(repo, "claim", "fix-B1")
    assert code == OK, err
    return _worktree(repo, "fix-B1")


def test_a_fix_in_the_test_file_is_verified_against_the_pre_fix_test_files(repo):
    wt = _repo(repo, BROKEN, "test_v.py")
    (wt / "tests" / "test_v.py").write_text(FIXED)
    _commit(wt, "fix the fixture")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_v.py::test_v")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified", out.data
    (data,) = _fixed_events(repo)
    ev = data["regression_verify"]
    assert ev["verified_by"] == "pre-fix-test-files" and ev["test_only_fix"], ev
    assert ev["changed_files"] == ["tests/test_v.py"]


def test_a_test_only_fix_that_no_old_test_file_fails_on_is_refused_without_a_mutation(repo):
    wt = _repo(repo, FLAKY_BASE, "test_h.py")
    (wt / "tests" / "test_h.py").write_text(GUARDED)
    _commit(wt, "guard")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_h.py::test_h")
    assert out.exit == REFUSED, out
    assert "mutation" in out.reason and "--skip-regression-verify" in out.reason, out.reason
    assert not _fixed_events(repo)


def test_a_mutation_that_makes_the_test_fail_verifies_by_mutation(repo):
    wt = _repo(repo, FLAKY_BASE, "test_h.py")
    (wt / "tests" / "test_h.py").write_text(GUARDED)
    _commit(wt, "guard")
    out = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_h.py::test_h",
        mutation={"file": "tests/test_h.py", "old": "GUARD = True", "new": "GUARD = False"},
    )
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified-by-mutation", out.data
    (data,) = _fixed_events(repo)
    ev = data["regression_verify"]
    assert ev["mutation"] == {
        "file": "tests/test_h.py",
        "old": "GUARD = True",
        "new": "GUARD = False",
    }
    assert ev["mutated_outcome"] == "failed" and ev["mutated_run"]["exit"] != 0, ev


def test_a_mutation_the_test_survives_or_that_misses_the_fix_is_refused(repo):
    wt = _repo(repo, FLAKY_BASE, "test_h.py")
    (wt / "tests" / "test_h.py").write_text(GUARDED)
    _commit(wt, "guard")
    survives = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_h.py::test_h",
        mutation={"file": "tests/test_h.py", "old": "GUARD = True", "new": "GUARD = 1"},
    )
    assert survives.exit == REFUSED and "still PASSES" in survives.reason, survives
    elsewhere = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_h.py::test_h",
        mutation={"file": "tests/other.py", "old": "x", "new": "y"},
    )
    assert elsewhere.exit == REFUSED and "does not change" in elsewhere.reason, elsewhere
    assert not _fixed_events(repo)


def test_the_mutation_flags_reach_complete_and_bug_fixed_on_the_cli(repo):
    wt = _repo(repo, FLAKY_BASE, "test_h.py")
    (wt / "tests" / "test_h.py").write_text(GUARDED)
    _commit(wt, "guard")
    code, out, err = run_cli(
        repo,
        "bug",
        "fixed",
        "B1",
        "--regression-test",
        "tests/test_h.py::test_h",
        "--mutation-file",
        "tests/test_h.py",
        "--mutation-old",
        "GUARD = True",
        "--mutation-new",
        "GUARD = False",
    )
    assert code == OK, err
    assert "verified-by-mutation" in out, out
    _, helptext, _ = run_cli(repo, "complete", "--help")
    assert "--mutation-file" in helptext


def test_a_fix_that_changes_source_still_needs_the_test_to_fail_first(repo):
    wt = _repo(repo, FLAKY_BASE, "test_h.py")
    (wt / "src.py").write_text("X = 1\n")
    (wt / "tests" / "test_h.py").write_text(GUARDED)
    _commit(wt, "source and test")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_h.py::test_h")
    assert out.exit == REFUSED and "pre-fix tree" in out.reason, out
    assert "test_only_fix" not in out.reason
