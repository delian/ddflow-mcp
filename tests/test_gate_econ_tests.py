"""D-gate-economy 1: once the ci gate has passed the whole suite on an item's tree, the
unit_tests gate of a bug fix or a small task runs only the tests its change reaches, and
says which and why. Anything else -- no passing ci on this tree, a larger task, any
selection that cannot be made -- runs the whole suite, and says why (B-gate-econ-tests).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

from ddflow.config import KNOB_CHOICES, KNOB_DOCS, KNOB_STRICTEST, Config

OK, FAIL = 0, 1

#: A test of `src.f` that passes once the fix makes it return 2.
REACHES_F = (
    "import pathlib, sys\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
    "from src import f\n\n\n"
    "def test_f():\n"
    "    assert f() == 2\n"
)
#: A test the change does not reach, failing: it shows whether the whole suite ran.
UNRELATED_RED = "def test_elsewhere_is_red():\n    assert False\n"

CI_PIPELINE = json.dumps(["research", "implement", "standards", "ci", "unit_tests", "merge"])


def _commit(tree: Path, msg: str) -> None:
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", msg)


def _cli(repo: Path, *argv: str) -> str:
    code, out, err = run_cli(repo, *argv)
    assert code == OK, (argv, out, err)
    return out


def _project(repo: Path) -> None:
    _cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_elsewhere.py").write_text(UNRELATED_RED)
    _cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q tests")
    _cli(repo, "config", "--set", "gates.task_pipeline", CI_PIPELINE)
    _commit(repo, "a project whose suite has one unrelated red test")


def _worktree(repo: Path, item: str) -> Path:
    raw = json.loads(_cli(repo, "--json", "show", item))["worktree"]
    return Path(raw) if Path(raw).is_absolute() else repo / raw


def _fix(repo: Path) -> Path:
    """Bug B1 and its fix task, claimed, with the fix and its test committed."""
    _cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    _cli(repo, "claim", "fix-B1")
    wt = _worktree(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2\n")
    (wt / "tests" / "test_f.py").write_text(REACHES_F)
    _commit(wt, "fix f")
    return wt


def _task(repo: Path, item: str, lines: int) -> Path:
    """A plain task, claimed, changing src.py by about ``lines`` lines plus its test."""
    _cli(repo, "task", "add", item, "--title", "work", "--globs", "src.py,tests/")
    _cli(repo, "claim", item)
    wt = _worktree(repo, item)
    filler = "".join(f"X{i} = {i}\n" for i in range(lines))
    (wt / "src.py").write_text("def f():\n    return 2\n" + filler)
    (wt / "tests" / "test_f.py").write_text(REACHES_F)
    _commit(wt, "change f")
    return wt


def _ci(repo: Path, item: str, outcome: str = "passed") -> None:
    """The ci gate's outcome on the item's tree as it is now (ddflow measures the tree)."""
    extra = [] if outcome == "passed" else ["--reason", "for the test"]
    _cli(repo, "gate", "record", item, "ci", "--outcome", outcome, "--evidence", "suite", *extra)


def _run_unit_tests(repo: Path, item: str) -> tuple[int, dict]:
    code, out, _err = run_cli(repo, "--json", "gate", "run", item, "unit_tests")
    body = json.loads(out)
    return code, body["evidence"]


def test_a_bug_fix_runs_only_the_tests_its_change_reaches(repo):
    _project(repo)
    _fix(repo)
    _ci(repo, "fix-B1")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == OK, ev
    assert ev["scope"] == "selected", ev
    assert "a bug fix (B1)" in ev["scope_why"]
    paths = {t["path"]: t["reason"] for t in ev["selected_tests"]}
    assert "tests/test_f.py" in paths and "tests/test_elsewhere.py" not in paths
    assert "tests/test_f.py" in ev["command"] and "test_elsewhere" not in ev["command"]


def test_without_a_passing_ci_the_fix_runs_the_whole_suite(repo):
    """roborev on 7da06042: a selection resting on a ci that never passed let a fix merge
    with the whole suite never run."""
    _project(repo)
    _fix(repo)
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL, ev  # the unrelated red test ran
    assert ev["scope"] == "full" and "ci gate has no outcome" in ev["scope_why"], ev
    assert "selected_tests" not in ev
    _ci(repo, "fix-B1", "skipped")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL and "ci gate is skipped" in ev["scope_why"], ev


def test_an_edit_after_ci_does_not_select(repo):
    _project(repo)
    wt = _fix(repo)
    _ci(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2  # edited after ci\n")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL, ev
    assert ev["scope"] == "full" and "the tree changed since the ci gate passed" in ev["scope_why"]
    _commit(wt, "the edit, committed")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL and "the tree changed since" in ev["scope_why"], ev


def test_a_ci_pass_on_a_dirty_tree_does_not_select_even_once_committed(repo):
    """roborev on 5ddf11f7: ci tests the committed HEAD, so a pass recorded over
    uncommitted edits never ran them; committing them afterwards must not make it count."""
    _project(repo)
    _cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    _cli(repo, "claim", "fix-B1")
    wt = _worktree(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2\n")
    (wt / "tests" / "test_f.py").write_text(REACHES_F)
    _ci(repo, "fix-B1")  # over uncommitted edits
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL and "uncommitted changes" in ev["scope_why"], ev
    _commit(wt, "fix f")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL and "uncommitted changes" in ev["scope_why"], ev


def test_without_a_ci_gate_in_the_pipeline_it_says_so(repo):
    _project(repo)
    _cli(
        repo,
        "config",
        "--set",
        "gates.task_pipeline",
        json.dumps(["implement", "unit_tests", "merge"]),
    )
    _fix(repo)
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL, ev
    assert "no ci gate in this item's pipeline" in ev["scope_why"], ev


def test_scope_full_runs_the_whole_suite(repo):
    _project(repo)
    _fix(repo)
    _ci(repo, "fix-B1")
    _cli(repo, "config", "--set", "gates.unit_tests_scope", "full")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL, ev
    assert ev["scope"] == "full" and 'unit_tests_scope = "full"' in ev["scope_why"], ev


def test_a_small_task_runs_the_selection(repo):
    _project(repo)
    _task(repo, "T1", lines=5)
    _ci(repo, "T1")
    code, ev = _run_unit_tests(repo, "T1")
    assert code == OK, ev
    assert ev["scope"] == "selected" and "a small task" in ev["scope_why"], ev
    assert ev["changed_lines"] < 150


def test_a_larger_task_runs_the_whole_suite(repo):
    _project(repo)
    _cli(repo, "config", "--set", "gates.unit_tests_small_lines", "20")
    _task(repo, "T1", lines=40)
    _ci(repo, "T1")
    code, ev = _run_unit_tests(repo, "T1")
    assert code == FAIL, ev
    assert ev["scope"] == "full" and "not a bug fix" in ev["scope_why"], ev
    assert ev["changed_lines"] >= 20


def test_a_change_no_test_reaches_runs_the_whole_suite_rather_than_nothing(repo):
    _project(repo)
    _cli(repo, "bug", "found", "--id", "B1", "--summary", "docs are wrong")
    _cli(repo, "claim", "fix-B1")
    wt = _worktree(repo, "fix-B1")
    (wt / "NOTES.txt").write_text("fixed\n")
    _commit(wt, "a change no test reaches")
    _ci(repo, "fix-B1")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == FAIL, ev  # running nothing would have passed
    assert ev["scope"] == "full" and "no test reaches" in ev["scope_why"], ev


def test_the_knobs_are_declared_with_a_strict_fallback():
    cfg = Config()
    assert cfg.gates.unit_tests_scope == "selected"
    assert cfg.gates.unit_tests_small_lines == 150
    assert KNOB_CHOICES["gates.unit_tests_scope"] == ("selected", "full")
    assert KNOB_STRICTEST["gates.unit_tests_scope"][0] == "full"
    assert "D-gate-economy" in KNOB_DOCS["gates.unit_tests_scope"]
    assert KNOB_DOCS["gates.unit_tests_small_lines"]


def test_only_regression_tests_pytest_runs_are_listed_as_selected(repo):
    """roborev on 7da06042: a non-pytest regression check was listed as selected though
    the command built here never runs it."""
    _project(repo)
    wt = _fix(repo)
    (wt / "scripts").mkdir()
    (wt / "scripts" / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    _commit(wt, "a shell check")
    _cli(
        repo, "bug", "fixed", "B1",
        "--regression-test", "tests/test_f.py::test_f;scripts/check.sh",
        "--skip-regression-verify", "--verify-reason", "this test is about the listing",
    )  # fmt: skip
    _ci(repo, "fix-B1")
    code, ev = _run_unit_tests(repo, "fix-B1")
    assert code == OK, ev
    listed = {t["path"] for t in ev["selected_tests"]}
    assert "scripts/check.sh" not in listed and "tests/test_f.py" in listed, ev


def test_a_ci_pass_with_no_measured_tree_does_not_select(repo):
    """roborev on e1cbc816: an unmeasured ci pass said 'uncommitted changes'; it cannot
    tell which tree it passed on, and says so."""
    from ddflow.api._base import _load
    from ddflow.services import testselect as TS

    _project(repo)
    wt = _fix(repo)
    _ci(repo, "fix-B1")
    _log, cfg, st = _load(repo, "")
    st.items["fix-B1"].gates["ci"].evidence.pop("tree_sha", None)
    scope = TS.unit_tests_scope(cfg, st, st.items["fix-B1"], "pytest -q tests", wt)
    assert scope.scope == "full" and "no measured tree" in scope.why, scope
