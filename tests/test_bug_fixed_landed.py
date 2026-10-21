"""`bug fixed --landed <sha>`: close a bug whose fix is already on the base (B256be593f0).

A fix task claimed after its fix landed through some other commit has no commit ahead and
no landing of its own, so the pre-fix check could only be `could-not-run` and the item could
not be finished without an override. The caller names the landing commit: the regression test
must FAIL on its first parent and PASS on it (the same rule, on a tree that exists), and the
now-pointless fix task is ended with the commit named as the reason.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import ALWAYS_PASSES, FAILS_FIRST, OK, REFUSED, _commit

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _landed_elsewhere(repo: Path, test_src: str = FAILS_FIRST, name: str = "test_f.py") -> str:
    """Bug B1 with an UNCLAIMED fix task, whose fix lands on main by a plain commit."""
    run_cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    _commit(repo, "src, and a runner that can run a node id")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    (repo / "src.py").write_text("def f():\n    return 2\n")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / name).write_text(test_src)
    _commit(repo, "fix f, elsewhere")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "later.txt").write_text("unrelated\n")
    _commit(repo, "a later commit: the base has moved on")
    return sha


def _item(repo: Path, item: str):
    return fold(EventLog(repo).read_all()).items[item]


def test_a_fix_landed_elsewhere_is_verified_against_its_landing_commit(repo):
    sha = _landed_elsewhere(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["regression_verified"] == "verified", res.data
    ev = res.data["regression_verify"]
    assert ev["prefix_outcome"] == "failed" and ev["fix_outcome"] == "passed", ev
    assert ev["tree"] == sha
    assert res.data["landed"] == sha
    assert [e.data["landed"] for e in EventLog(repo).read_all() if e.kind == "bug.fixed"] == [sha]


def test_the_fix_task_is_ended_naming_the_landing(repo):
    sha = _landed_elsewhere(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["fix_task_removed"] == "fix-B1", res.data
    events = [e for e in EventLog(repo).read_all() if e.kind == "task.removed"]
    assert sha[:12] in events[-1].data["reason"], events[-1].data


def test_a_test_that_already_passes_before_the_landing_is_refused(repo):
    sha = _landed_elsewhere(repo, ALWAYS_PASSES, "test_always.py")
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_always.py::test_always", landed=sha)
    assert res.exit == REFUSED, res
    assert "pre-fix tree" in res.reason
    assert not _item(repo, "fix-B1").removed, "a refused close leaves the fix task alone"


def test_a_commit_that_is_not_on_the_base_is_refused(repo):
    _landed_elsewhere(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "side"], check=True)
    (repo / "side.txt").write_text("x\n")
    _commit(repo, "only on a side branch")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"], check=True)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == REFUSED, res
    assert "not on" in res.reason


def test_an_unknown_commit_is_refused(repo):
    _landed_elsewhere(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed="deadbeef")
    assert res.exit == REFUSED, res
    assert "deadbeef" in res.reason


def test_the_cli_takes_landed(repo):
    sha = _landed_elsewhere(repo)
    code, out, err = run_cli(
        repo, "bug", "fixed", "B1", "--regression-test", "tests/test_f.py::test_f", "--landed", sha
    )
    assert code == OK, out + err
    assert "[verified]" in out and sha[:12] in out, out


def test_a_task_held_by_someone_else_is_kept(repo):
    sha = _landed_elsewhere(repo)
    code, out, err = run_cli(repo, "--agent", "other", "claim", "fix-B1")
    assert code == OK, out + err
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["fix_task_kept"] == "held", res.data
    assert not _item(repo, "fix-B1").removed


def test_a_close_without_landed_records_no_landing_and_leaves_the_task(repo):
    """The opt-in must not leak into ordinary closes (a falsy `landing`, not a tuple)."""
    _landed_elsewhere(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f")
    assert res.exit == OK, res.reason
    assert "landed" not in res.data and "fix_task_removed" not in res.data, res.data
    ev = [e.data for e in EventLog(repo).read_all() if e.kind == "bug.fixed"][-1]
    assert "landed" not in ev, ev
    assert not _item(repo, "fix-B1").removed
