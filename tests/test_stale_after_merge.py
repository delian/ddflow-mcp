"""complete's "passed on a different tree" NOTE fires only when the tree really differs.

Bugs Bd86b05a8f8, Ba84119f707, B613cb67194: after `ddflow merge` (which removes the
item's worktree) every evidence gate was reported stale, even ones recorded at the
branch's final commit. `verdict` fingerprinted the PRIMARY checkout instead -- its HEAD
is main, and the fingerprint is a commit id plus dirt -- so every completion on the
ordinary path (merge, then complete) warned, and a warning that always fires teaches
everyone to ignore the one that is real.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import pass_pipeline, run_cli

OK = 0
NOTE = "passed on a different tree"


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _claimed(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "true"\ncwd = "worktree"\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == OK, out + err
    tree = Path(next(ln.split(":", 1)[1].strip() for ln in out.splitlines() if "worktree:" in ln))
    assert tree.is_dir()
    return tree


def _merge_and_complete(repo: Path) -> str:
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == OK, out + err
    pass_pipeline(repo, "T1", omit=("unit_tests", "merge"))
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5")
    assert code == OK, out + err
    return out + err


def test_a_gate_run_at_the_branch_head_is_not_stale_after_merge(repo):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    assert run_cli(tree, "gate", "run", "T1", "unit_tests")[0] == OK
    # The primary is busy with other people's work, as it is in practice.
    (repo / "someone-elses.txt").write_text("not T1's\n")
    said = _merge_and_complete(repo)
    assert NOTE not in said, said


def test_a_gate_run_on_uncommitted_edits_that_were_then_committed_is_not_stale(repo):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")  # untracked, exactly as committed below
    assert run_cli(tree, "gate", "run", "T1", "unit_tests")[0] == OK
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    said = _merge_and_complete(repo)
    assert NOTE not in said, said


def test_an_edit_after_the_gate_that_landed_is_still_reported_and_named(repo):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    assert run_cli(tree, "gate", "run", "T1", "unit_tests")[0] == OK
    (tree / "a.py").write_text("a = 2\n")
    _git(tree, "commit", "-qam", "one more thing")
    said = _merge_and_complete(repo)
    assert "unit_tests " + NOTE in said, said
    assert "a.py" in said, f"the NOTE should say what differs: {said}"


def test_an_edit_after_the_gate_before_merge_is_still_reported(repo):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    assert run_cli(tree, "gate", "run", "T1", "unit_tests")[0] == OK
    (tree / "a.py").write_text("a = 2\n")
    pass_pipeline(repo, "T1", omit=("unit_tests", "merge"))
    run_cli(repo, "gate", "record", "T1", "merge", "--outcome", "passed", "--evidence", "x")
    _code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5", "--force")
    assert "unit_tests " + NOTE in out + err, out + err


def test_source_tree_of_a_dirty_tree_equals_the_commit_that_records_it(repo):
    """The identity underneath: the content id of a working tree is the content id of
    the commit that later records exactly those files, and `.ddflow/` does not count."""
    from ddflow.services import gates as G

    (repo / "sub").mkdir()
    (repo / "sub" / "b.py").write_text("b\n")
    (repo / "README.md").write_text("# changed\n")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "noise.jsonl").write_text("{}\n")
    dirty = G.source_tree(repo)
    assert dirty
    _git(repo, "add", "sub/b.py", "README.md")
    _git(repo, "commit", "-qm", "c")
    assert G.commit_source_tree(repo, "HEAD") == dirty
    assert G.source_tree(repo) == dirty
    (repo / "sub" / "b.py").write_text("b2\n")
    assert G.source_tree(repo) != dirty


def _legacy_pass(repo: Path, tree_sha: str) -> None:
    """A unit_tests pass as recorded before `source_tree` existed: a fingerprint only."""
    from ddflow.infra.log import EventLog

    EventLog(repo, "legacy-agent").append(
        "gate.passed",
        "T1",
        {"gate": "unit_tests", "by": "legacy-agent", "reason": "",
         "evidence": {"command": "true", "exit": 0, "tree_sha": tree_sha}},
    )  # fmt: skip


#: How a clean tree's fingerprint was spelled before bug B-fingerprint-never-clean.
LEGACY_CLEAN = "95e0c70caf8cc336"


@pytest.mark.parametrize("clean", ["clean", LEGACY_CLEAN])
def test_legacy_evidence_on_a_clean_branch_head_is_not_stale_after_merge(repo, clean):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    _legacy_pass(repo, _git(tree, "rev-parse", "HEAD")[:12] + "+" + clean)
    said = _merge_and_complete(repo)
    assert NOTE not in said, said


@pytest.mark.parametrize("clean", ["clean", LEGACY_CLEAN])
def test_legacy_evidence_on_an_older_commit_is_stale_after_merge(repo, clean):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    _legacy_pass(repo, _git(tree, "rev-parse", "HEAD")[:12] + "+" + clean)
    (tree / "a.py").write_text("a = 2\n")
    _git(tree, "commit", "-qam", "one more thing")
    said = _merge_and_complete(repo)
    assert "unit_tests " + NOTE in said and "a.py" in said, said


def test_a_fast_forwarded_branch_head_that_is_itself_a_merge_is_what_landed(repo):
    """Main merged INTO the branch, then the branch fast-forwards main: the landed head
    is a merge commit, but not ddflow's -- its second parent is main's, not the work."""
    tree = _claimed(repo)
    assert run_cli(repo, "config", "--set", "worktree.merge_strategy", "ff-only")[0] == OK
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    (repo / "m.txt").write_text("main moved\n")
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-qm", "main moves")
    _git(tree, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    assert run_cli(tree, "gate", "run", "T1", "unit_tests")[0] == OK
    head = _git(tree, "rev-parse", "HEAD")
    said = _merge_and_complete(repo)
    assert _git(repo, "rev-parse", "main") == head, "expected a fast-forward"
    assert NOTE not in said, said


def test_a_clean_tree_fingerprints_as_clean(repo):
    """Bug B-fingerprint-never-clean: the three parts were joined with NUL and the
    joined body tested with `str.strip`, which keeps NUL -- so no tree was ever clean."""
    from ddflow.services import gates as G

    assert G.tree_fingerprint(repo).endswith("+clean"), G.tree_fingerprint(repo)
    assert G.digest("\x00\x00") == G.LEGACY_CLEAN == LEGACY_CLEAN
    (repo / "new.py").write_text("x\n")
    assert not G.tree_fingerprint(repo).endswith("+clean")


def _commit_all_matches(repo: Path) -> None:
    """The content id of the working tree must equal that of the commit recording it."""
    from ddflow.services import gates as G

    before = G.source_tree(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "record it")
    assert G.commit_source_tree(repo, "HEAD") == before


def test_a_tracked_file_replaced_by_a_directory_leaves_no_phantom_entry(repo):
    (repo / "foo").write_text("file\n")
    _git(repo, "add", "foo")
    _git(repo, "commit", "-qm", "foo")
    (repo / "foo").unlink()
    (repo / "foo").mkdir()
    (repo / "foo" / "bar").write_text("hi\n")
    _commit_all_matches(repo)


def test_without_filemode_a_new_executable_is_recorded_as_git_records_it(repo):
    _git(repo, "config", "core.fileMode", "false")
    run = repo / "run.sh"
    run.write_text("#!/bin/sh\n")
    run.chmod(0o755)
    _commit_all_matches(repo)


def test_with_filemode_an_executable_is_recorded_as_git_records_it(repo):
    run = repo / "run.sh"
    run.write_text("#!/bin/sh\n")
    run.chmod(0o755)
    other = repo / "group.sh"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o654)  # group-executable only: git records 100644
    _commit_all_matches(repo)


def test_a_forge_fast_forward_of_a_merge_head_is_what_landed(repo):
    """The forge path records `landed_before` as the landed commit's own first parent,
    so only the PR's head tells a fast-forwarded merge head from ddflow's merge."""
    from ddflow.core.model import Item, PullRequest
    from ddflow.services.completion import _tree_being_completed

    _git(repo, "checkout", "-qb", "feature")
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "T1")
    _git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("main moved\n")
    _git(repo, "add", "m.txt")
    _git(repo, "commit", "-qm", "main moves")
    main_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    head = _git(repo, "rev-parse", "HEAD")
    it = Item(id="T1", kind="task")
    it.landed_after = head
    it.landed_before = _git(repo, "rev-parse", "HEAD^1")
    it.merged_sha = head
    it.pr = PullRequest(head_sha=head, merge_sha=head, state="merged")
    assert _tree_being_completed(repo, it) == (repo, head)
    it.pr = None  # without the PR's head the first-parent rule takes the second parent
    assert _tree_being_completed(repo, it) == (repo, main_tip)


def test_legacy_evidence_on_uncommitted_edits_is_unverified_after_merge_not_fresh(repo):
    tree = _claimed(repo)
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    _legacy_pass(repo, _git(tree, "rev-parse", "HEAD")[:12] + "+0123456789abcdef")
    said = _merge_and_complete(repo)
    assert NOTE not in said, said
    assert (
        "unit_tests passed, but whether on the tree you are completing could not be checked" in said
    ), said
