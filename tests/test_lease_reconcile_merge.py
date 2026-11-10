"""B-lease-reconcile-merge: merging files another item changed since this one forked records
`merge.reconciled`, and complete is refused until the affected tests pass on the reconciled
tree (D-lease-hold-limit-r2, D-gate-economy-r3)."""

from __future__ import annotations

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import completion as CM


def _state(repo):
    return fold(EventLog(repo).read_all())


def _reconciled(repo, item: str, files: list[str]) -> None:
    EventLog(repo).append(
        "merge.reconciled", item, {"files": files, "base": "0" * 40, "main": "0" * 40}
    )


def test_reconcile_tests_defaults_to_affected(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    assert Config.load(repo).lease.reconcile_tests == "affected"


def test_a_reconciled_merge_folds_onto_the_item_and_refuses_complete(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    _reconciled(repo, "T1", ["src/x.py"])
    st = _state(repo)
    assert st.items["T1"].reconciled["files"] == ["src/x.py"]
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("reconciled src/x.py" in b for b in v.blockers), v.blockers


def test_off_disables_the_reconcile_guard(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    run_cli(repo, "config", "--set", "lease.reconcile_tests", "off")
    _reconciled(repo, "T1", ["src/x.py"])
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert not any("reconciled" in b for b in v.blockers), v.blockers


def test_a_reconciled_merge_with_a_passed_ci_completes(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    _reconciled(repo, "T1", ["src/x.py"])
    # A passed ci gate recorded AFTER the reconcile (on the reconciled tree, its selection
    # reached by the reconciled src/x.py) is the affected-tests evidence.
    run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "ci",
        "--outcome",
        "passed",
        "--evidence",
        "affected tests on the reconciled tree: tests/test_x.py (reached by src/x.py)",
    )
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert not any("reconciled" in b for b in v.blockers), v.blockers


def test_a_stale_pre_reconcile_pass_does_not_clear_the_guard(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    # A ci pass recorded BEFORE the reconcile ran on the pre-merge tree: it must NOT clear
    # the reconcile guard, which asks for the tests on the RECONCILED tree.
    run_cli(
        repo, "gate", "record", "T1", "ci", "--outcome", "passed", "--evidence", "pre-merge run"
    )
    _reconciled(repo, "T1", ["src/x.py"])
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("reconciled src/x.py" in b for b in v.blockers), v.blockers


def test_a_post_reconcile_full_pass_clears_the_guard_under_full(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    run_cli(repo, "config", "--set", "lease.reconcile_tests", "full")
    _reconciled(repo, "T1", ["src/x.py"])
    # Under 'full', a ci pass (the selection) is NOT enough: only the whole suite passes.
    run_cli(
        repo, "gate", "record", "T1", "ci", "--outcome", "passed", "--evidence", "selected tests"
    )
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("reconciled src/x.py" in b for b in v.blockers), v.blockers
