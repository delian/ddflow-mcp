"""[worktree].local_files: git-ignored, machine-local files reach every worktree.

Decision D-roborev-local (operator, 2026-09-29): a tool configuration that must not be
committed -- roborev's .roborev.toml, naming one machine's agent and model -- is still
read from each checkout, and a worktree is a checkout that simply does not have it. So
ddflow copies the listed files in from the primary, and nothing else.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra import worktree as W


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _setup(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / ".gitignore").write_text(".roborev.toml\n.ddflow/index.db*\n")
    (repo / ".roborev.toml").write_text("agent = 'kilo'\n")
    (repo / "tracked.toml").write_text("committed = true\n")
    code, _out, err = run_cli(
        repo, "config", "--set", "worktree.local_files",
        ".roborev.toml,tracked.toml,missing.toml,../outside.toml",
    )  # fmt: skip
    assert code == 0, err
    (repo.parent / "outside.toml").write_text("not ours\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "setup")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    return repo


def test_a_new_worktree_gets_the_git_ignored_local_file(repo):
    _setup(repo)
    code, _out, err = run_cli(repo, "claim", "T1")
    assert code == 0, err
    tree = next(
        Path(w["worktree"]) for w in W.list_worktrees(repo) if w.get("branch", "").endswith("T1")
    )
    assert (tree / ".roborev.toml").read_text() == "agent = 'kilo'\n"


def test_only_the_listed_untracked_files_inside_the_repo_are_copied(repo, cfg):
    _setup(repo)
    tree = repo.parent / "trees" / "wt"  # nested, so "../outside.toml" lands in trees/
    _git(repo, "worktree", "add", "-q", "-b", "x", str(tree))
    (tree / "tracked.toml").write_text("edited in the worktree\n")
    copied = W.copy_local_files(
        repo, tree, [".roborev.toml", "tracked.toml", "missing.toml", "../outside.toml"]
    )
    assert copied == [".roborev.toml"]
    assert (tree / "tracked.toml").read_text() == "edited in the worktree\n", (
        "a tracked path arrives with the checkout and is never overwritten"
    )
    assert not (tree.parent / "outside.toml").exists(), "a path outside the repo is not copied"


def test_a_file_already_in_the_worktree_is_never_overwritten(repo):
    _setup(repo)
    tree = repo.parent / "wt2"
    _git(repo, "worktree", "add", "-q", "-b", "y", str(tree))
    (tree / ".roborev.toml").write_text("agent = 'mine'\n")
    assert W.copy_local_files(repo, tree, [".roborev.toml"]) == []
    assert (tree / ".roborev.toml").read_text() == "agent = 'mine'\n"


def test_a_tracked_file_deleted_in_the_worktree_is_not_brought_back(repo):
    """A tracked path is the checkout's business: an agent that deleted it on purpose
    must not find the primary's copy resurrected by the next claim."""
    _setup(repo)
    tree = repo.parent / "wt3"
    _git(repo, "worktree", "add", "-q", "-b", "z", str(tree))
    (tree / "tracked.toml").unlink()
    assert W.copy_local_files(repo, tree, ["tracked.toml"]) == []
    assert not (tree / "tracked.toml").exists()


def test_a_file_that_cannot_be_copied_is_skipped_not_raised(repo, monkeypatch):
    """Bug B3254e02e1f: an OSError from the copy (an unreadable source, a full disk)
    escaped `W.create`, and claim -- which catches only GitError -- died with a
    traceback after the worktree existed but before it was recorded."""
    _setup(repo)
    (repo / ".other.toml").write_text("x\n")
    tree = repo.parent / "wt4"
    _git(repo, "worktree", "add", "-q", "-b", "w", str(tree))
    real = W.shutil.copy2

    def copy2(src, dst, *a, **k):
        if Path(src).name == ".roborev.toml":
            raise PermissionError(13, "Permission denied", str(src))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(W.shutil, "copy2", copy2)
    assert W.copy_local_files(repo, tree, [".roborev.toml", ".other.toml"]) == [".other.toml"]
    assert not (tree / ".roborev.toml").exists()


def test_a_harness_tree_claim_adopts_gets_the_local_file(repo):
    """Bug B41902e229d: claim binds a tree in three ways and only `W.create` copied
    the local files. A harness tree adopted where the agent stands got none of them."""
    from ddflow.api import lifecycle

    _setup(repo)
    tree = repo.parent / "harness"
    _git(repo, "worktree", "add", "-q", "-b", "harness", str(tree))
    out = lifecycle.claim(repo, "T1", called_from=tree)
    assert out.exit == 0, out.reason
    assert Path(out.data["worktree"]).resolve() == tree.resolve()
    assert (tree / ".roborev.toml").read_text() == "agent = 'kilo'\n"


def test_a_reclaim_that_rebinds_the_items_tree_gets_the_local_file(repo):
    """Bug B41902e229d: a re-claim binds the item's recorded tree without `W.create`,
    so a tree made before the knob listed a file never received it."""
    from ddflow.api import lifecycle

    _setup(repo)
    code, _out, err = run_cli(repo, "claim", "T1")
    assert code == 0, err
    tree = next(
        Path(w["worktree"]) for w in W.list_worktrees(repo) if w.get("branch", "").endswith("T1")
    )
    (tree / ".roborev.toml").unlink()  # as if made before the knob listed it
    assert run_cli(repo, "release", "T1")[0] == 0
    out = lifecycle.claim(repo, "T1")
    assert out.exit == 0, out.reason
    assert out.data["rebound"] is True
    assert (tree / ".roborev.toml").read_text() == "agent = 'kilo'\n"
