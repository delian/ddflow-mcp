"""`bug fixed` VERIFIES the regression test: it must FAIL without the fix (B-bugfix-verified).

"A bug is not closed without a regression test that fails against the unfixed code" was
prose: `bug fixed` only checked that `--regression-test` was non-empty. Now the named test
is RUN -- through the unit_tests gate's own command, not a second runner -- on the pre-fix
source (the fix task's base with the new test file) and on the fixed tree. A test that
PASSES on the pre-fix tree is refused (it does not catch the bug); a check that could not
be made is recorded `could-not-run`, never `verified`; and an override needs a recorded
reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3

#: Fails while `src.f()` returns 1 (the base) and passes once it returns 2 (the fix).
FAILS_FIRST = (
    "import pathlib, sys\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
    "from src import f\n\n\n"
    "def test_f():\n"
    "    assert f() == 2\n"
)

ALWAYS_PASSES = "def test_always():\n    assert True\n"


def _commit(tree: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", msg], check=True, capture_output=True)


def _worktree(repo: Path, item: str) -> Path:
    code, out, err = run_cli(repo, "--json", "show", item)
    assert code == OK, err
    raw = json.loads(out)["worktree"]
    assert raw, f"{item} has no worktree"
    tree = Path(raw)
    return tree if tree.is_absolute() else repo / tree


def _case(repo: Path) -> Path:
    """A repo with bug B1 and its fix task in a worktree: `src.f()` returns 2 there and 1
    on main, and `tests/test_f.py` fails without the fix and passes with it."""
    run_cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    run_cli(
        repo,
        "config",
        "--set",
        "gate.unit_tests.command",
        f"{sys.executable} -m pytest -q",
    )
    _commit(repo, "src, and a runner that can run a node id")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    code, _out, err = run_cli(repo, "claim", "fix-B1")
    assert code == OK, err
    wt = _worktree(repo, "fix-B1")
    (wt / "src.py").write_text("def f():\n    return 2\n")
    (wt / "tests").mkdir(exist_ok=True)
    (wt / "tests" / "test_f.py").write_text(FAILS_FIRST)
    _commit(wt, "fix f")
    return wt


def _fixed_events(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo).read_all() if e.kind == "bug.fixed"]


def test_a_test_that_fails_without_the_fix_and_passes_with_it_is_verified(repo):
    _case(repo)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified", out.data
    (data,) = _fixed_events(repo)
    assert data["regression_verified"] == "verified"
    assert data["regression_verify"]["prefix_outcome"] == "failed", "fails on the base"
    assert data["regression_verify"]["fix_outcome"] == "passed", "passes with the fix"


def test_a_test_that_passes_on_the_pre_fix_tree_is_refused(repo):
    wt = _case(repo)
    (wt / "tests" / "test_always.py").write_text(ALWAYS_PASSES)
    _commit(wt, "an always-passing test")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_always.py::test_always")
    assert out.exit == REFUSED, out
    assert "pre-fix tree" in out.reason
    assert not _fixed_events(repo), "a refused close writes no bug.fixed"


def test_a_bug_whose_fix_task_is_not_in_a_worktree_is_could_not_run(repo):
    _case(repo)
    run_cli(repo, "bug", "found", "--id", "B2", "--summary", "unclaimed fix")
    out = api.bug_fixed(repo, "B2", regression_test="tests/test_f.py::test_f")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "could-not-run", out.data


def test_the_override_needs_a_reason_and_is_recorded(repo):
    _case(repo)
    run_cli(repo, "bug", "found", "--id", "B3", "--summary", "override")
    no_reason = api.bug_fixed(
        repo,
        "B3",
        regression_test="tests/test_f.py::test_f",
        verify_regression=False,
        verify_reason="",
    )
    assert no_reason.exit == REFUSED and "verify-reason" in no_reason.reason, no_reason
    out = api.bug_fixed(
        repo,
        "B3",
        regression_test="tests/test_f.py::test_f",
        verify_regression=False,
        verify_reason="the runner is not installed on this machine",
    )
    assert out.exit == OK and out.data["regression_verified"] == "overridden", out
    (data,) = _fixed_events(repo)
    assert data["regression_verify"]["reason"].startswith("the runner is not installed")


def test_a_spec_that_is_not_a_pytest_node_id_is_not_applicable(repo):
    _case(repo)
    run_cli(repo, "bug", "found", "--id", "B4", "--summary", "shell spec")
    out = api.bug_fixed(repo, "B4", regression_test="scripts/check.sh")
    assert out.exit == OK and out.data["regression_verified"] == "not-applicable", out


def test_no_runner_configured_records_could_not_run(repo):
    from ddflow.config import Config
    from ddflow.services.gates import GateDef, verify_regression_test

    _case(repo)
    status, ev = verify_regression_test(
        repo,
        Config.load(),
        tree=_worktree(repo, "fix-B1"),
        base="main",
        tests=["tests/test_f.py::test_f"],
        gates={"unit_tests": GateDef(id="unit_tests", command="")},
    )
    assert status == "could-not-run", status
    assert "pytest" in ev["reason"], ev


def test_cli_skip_verify_requires_a_reason_then_records_the_override(repo):
    _case(repo)
    run_cli(repo, "bug", "found", "--id", "B5", "--summary", "cli override")
    code, _out, err = run_cli(
        repo,
        "bug",
        "fixed",
        "B5",
        "--regression-test",
        "tests/test_f.py::test_f",
        "--skip-regression-verify",
    )
    assert code == REFUSED and "verify-reason" in err, err
    code, out, err = run_cli(
        repo,
        "bug",
        "fixed",
        "B5",
        "--regression-test",
        "tests/test_f.py::test_f",
        "--skip-regression-verify",
        "--verify-reason",
        "runner not installed here",
    )
    assert code == OK, err
    assert "overridden" in out, out


def test_mcp_offers_the_verify_knobs():
    from ddflow.surfaces.mcp import TOOLS, _schema

    props = _schema(TOOLS["ddflow_bug_fixed"])["properties"]
    assert props["verify_regression"]["type"] == "boolean"
    assert props["verify_reason"]["type"] == "string"
