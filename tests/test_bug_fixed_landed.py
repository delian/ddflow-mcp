"""`bug fixed --landed <sha>`: close a bug whose fix is already on the base (B256be593f0).

A fix task claimed after its fix landed through some other commit has no commit ahead and
no landing of its own, so the pre-fix check could only be `could-not-run` and the item could
not be finished without an override. The caller names the landing commit: the regression test
must FAIL on its first parent and PASS on it (the same rule, on a tree that exists), and the
now-pointless fix task is ended with the commit named as the reason.
"""

from __future__ import annotations

import json
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


def _landed_with_invalid_committed_base_ref(repo: Path) -> str:
    """A landing commit whose COMMITTED config leaks a flag into `[worktree].base_ref`,
    exactly the historical `--local` leak B20cfd41344 is about. Both the landing commit AND
    its first parent (the pre-fix source) carry the value `Config.load` refuses, so the test
    exercises BOTH scratch checkouts ddflow builds. The base has since moved on with a valid
    config, so only those checkouts still carry the value."""
    run_cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    _commit(repo, "src, and a runner that can run a node id")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    cfgp = repo / ".ddflow" / "config.toml"
    before = cfgp.read_text()
    injected = before.replace(
        "[worktree]\nenabled = true\n",
        '[worktree]\nenabled = true\nbase_ref = "--local"\n',
    )
    assert injected != before and 'base_ref = "--local"' in injected, (
        "expected the scaffold's [worktree].enabled = true block to inject the broken base_ref"
    )
    cfgp.write_text(injected)
    # The PRE-FIX source carries the broken config: the parent of the landing commit.
    _commit(repo, "leak a flag into [worktree].base_ref")
    (repo / "src.py").write_text("def f():\n    return 2\n")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_f.py").write_text(FAILS_FIRST)
    _commit(repo, "fix f, elsewhere (its committed config still leaks the flag)")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    # The base moves on with a valid config again: only the LANDING commit and its parent
    # carry the broken value, as this project's history actually does.
    cfgp.write_text(before)
    (repo / "later.txt").write_text("unrelated\n")
    _commit(repo, "a later commit: the base has moved on with a valid config again")
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


def test_a_landing_whose_committed_config_is_invalid_uses_the_callers_config(repo):
    """B20cfd41344: the fixed/pre-fix run must not reload config from a historical commit
    it checks out. One that committed `base_ref = "--local"` makes `Config.load` raise
    InvalidValue (a value starting with '-' is a flag, not a ref), which used to crash the
    whole `bug fixed --landed` before the check even ran. The project's own config governs
    the run, so the close succeeds."""
    sha = _landed_with_invalid_committed_base_ref(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["regression_verified"] == "verified", res.data


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


def test_the_callers_own_claim_on_the_fix_task_is_ended_and_released(repo):
    """The bug's own scenario: the fix task was CLAIMED before the close, by the closer."""
    sha = _landed_elsewhere(repo)
    code, out, err = run_cli(repo, "claim", "fix-B1")
    assert code == OK, out + err
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["fix_task_abandoned"] == "fix-B1", res.data
    task = _item(repo, "fix-B1")
    assert task.lease is None and task.state == "abandoned", (task.state, task.lease)


def test_the_base_tip_itself_can_be_the_landing(repo):
    sha = _landed_elsewhere(repo)
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--hard", sha], check=True)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f", landed=sha)
    assert res.exit == OK, res.reason
    assert res.data["regression_verified"] == "verified", res.data


def test_landed_without_a_node_id_is_refused_not_closed_unverified(repo):
    sha = _landed_elsewhere(repo)
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py", landed=sha)
    assert res.exit == REFUSED, res
    assert "node id" in res.reason
    assert not _item(repo, "fix-B1").removed


def test_a_mixed_list_with_one_unchecked_entry_is_refused(repo):
    sha = _landed_elsewhere(repo)
    res = api.bug_fixed(
        repo, "B1", regression_test=["tests/test_f.py::test_f", "tests/test_f.py"], landed=sha
    )
    assert res.exit == REFUSED, res
    assert "tests/test_f.py could not be checked" in res.reason
    assert not _item(repo, "fix-B1").removed


def test_a_merged_fix_task_is_kept_not_removed(repo):
    """B-bug-fixed-removes-merged-fix-task: a fix task whose fix lands through ddflow's
    OWN merge already reached the base, so `bug fixed --landed <sha>` must not REMOVE it
    (the release at merge puts it back to OPEN, and the unlanded path deletes it). Its
    merged work is a done task waiting on `ddflow complete`, not a false finding to drop.
    Removing it destroyed the record of done gates and made it 'no such item'."""
    run_cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_f.py").write_text(FAILS_FIRST)
    _commit(repo, "src and the regression test")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "f returns 1")
    # The fix lands through ddflow's own merge, exactly the red-b sequence.
    code, out, err = run_cli(repo, "--json", "claim", "fix-B1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "src.py").write_text("def f():\n    return 2\n")
    _commit(tree, "fix f in the fix task's worktree")
    assert run_cli(repo, "merge", "fix-B1")[0] == 0
    merged = _item(repo, "fix-B1").merged_sha
    assert merged, "the fix must have actually landed through ddflow's merge"
    res = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_f.py::test_f",
        landed=merged,
        verify_regression=False,
        verify_reason="the merged work's own check is the regression test",
    )
    assert res.exit == OK, res.reason
    assert res.data["fix_task_kept"] == "finished", res.data
    assert res.data["fix_task_removed"] == "", res.data
    task = _item(repo, "fix-B1")
    assert not task.removed
    assert task.merged_sha == merged


def test_a_fix_task_with_only_landed_after_is_still_removed_not_kept(repo):
    """The critic's landed_after concern, guarded: a landing pair WITHOUT ddflow's own
    merge leaves merged_sha empty, so `landed_after` alone must NOT mark the task
    finished -- a false finding's fix task is still removed by `bug invalid`."""
    sha = _landed_elsewhere(repo)
    # A landing pair on the fix task with no ddflow merge (no worktree.merged -> no merged_sha).
    run_cli(repo, "update", "fix-B1", "--globs", "src.py")
    code, out, err = run_cli(repo, "update", "fix-B1", "--landed", sha)
    assert code == 0, out + err
    assert not _item(repo, "fix-B1").merged_sha
    res = api.bug_invalid(repo, "B1", reason="not a real bug")
    assert res.exit == OK, res.reason
    assert res.data["fix_task_removed"] == "fix-B1", res.data
    assert _item(repo, "fix-B1").removed
