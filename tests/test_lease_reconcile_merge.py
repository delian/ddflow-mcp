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


def _head(repo) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _reconciled(repo, item: str, files: list[str]) -> None:
    head = _head(repo)
    EventLog(repo).append("merge.reconciled", item, {"files": files, "base": head, "main": head})


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


def test_a_post_reconcile_ci_pass_does_not_clear_the_guard_under_full(repo):
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


def test_an_unknown_reconcile_fails_closed_and_still_blocks(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    # The merge could not read the changed files (git could not answer): the reconcile
    # records `unknown` and complete stays blocked, never silently cleared.
    EventLog(repo).append(
        "merge.reconciled",
        "T1",
        {"files": [], "base": "0" * 40, "main": "0" * 40, "unknown": True},
    )
    st = _state(repo)
    assert st.items["T1"].reconciled["unknown"] is True
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("could not read" in b for b in v.blockers), v.blockers


def test_a_post_reconcile_pass_on_another_tree_needs_reconciled_tree_evidence(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    EventLog(repo).append(
        "merge.reconciled",
        "T1",
        {"files": ["src/x.py"], "base": "a" * 40, "main": "b" * 40},
    )
    # A unit_tests pass recorded at/after the reconcile that names NEITHER the reconciled
    # commit nor the reconciled file is a stale pre-merge run, not proof the suite ran on
    # the reconciled tree: the guard must still hold.
    run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "unit_tests",
        "--outcome",
        "passed",
        "--evidence",
        "pre-merge run",
    )
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("reconciled src/x.py" in b for b in v.blockers), v.blockers


def test_a_pass_naming_the_file_but_a_different_tree_stays_fail_closed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")
    EventLog(repo).append(
        "merge.reconciled",
        "T1",
        {"files": ["src/x.py"], "base": "a" * 40, "main": "b" * 40},
    )
    # The evidence mentions the reconciled file, but its `tree_sha` names a DIFFERENT commit:
    # that is a stale checkout, not proof the suite ran on the reconciled tree.
    EventLog(repo).append(
        "gate.passed",
        "T1",
        {
            "gate": "unit_tests",
            "evidence": {
                "tree_sha": "c" * 12 + "+clean",
                "note": "pre-merge suite, touched src/x.py",
            },
        },
    )
    st = _state(repo)
    v = CM.verdict(st, Config.load(repo), "T1", repo=repo, model="claude-sonnet-5-5")
    assert any("reconciled src/x.py" in b for b in v.blockers), v.blockers


def test_a_missing_base_sha_records_an_unknown_reconcile_fail_closed(repo):
    import subprocess

    from ddflow.api.lifecycle.merge import _record_reconciled

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/x.py")

    def rev(ref: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    landed_before = rev("HEAD")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "x.py").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "item change"], check=True)
    branch_head = rev("HEAD")

    class It:
        base_sha = ""

    _record_reconciled(
        repo,
        Config.load(repo),
        EventLog(repo),
        It(),
        "T1",
        landed_before,
        landed_before,
        branch_head,
    )
    # No recorded fork means the fork is unanswerable, so the reconcile fails CLOSED with an
    # `unknown` stamp over MY files instead of silently skipping an overlapping merge.
    st = _state(repo)
    assert st.items["T1"].reconciled["unknown"] is True
    assert "src/x.py" in st.items["T1"].reconciled["files"]
