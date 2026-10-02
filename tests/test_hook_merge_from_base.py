"""The commit hook judges what a merge ADDS, not what it brings in (B07878037ab, part 2).

Committing a merge from main in an item's tree stages every path main changed. A clean
merge was already exempt (`clean_merge_conclusion`), but one conflict resolved by hand
made the hook list all of main's already-merged paths as "not covered by a lease you
hold" -- 17 of them in home-simulator's 34.8i -- burying the one path that was this
item's own. A path whose staged content equals the incoming parent's (MERGE_HEAD) came
from there unchanged and is not judged.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A

AGENT = "agent-merger"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _hook(repo: Path) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "DDFLOW_AGENT": AGENT,
    }
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "check-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return p.stdout + p.stderr


def _mid_merge(repo: Path, globs: str) -> None:
    """On branch `feat`, mid-merge of main with `shared.txt` resolved by hand; main also
    brought `from_main.txt` and `docs/other.md`, which feat never touched."""
    run_cli(repo, "init")
    (repo / "shared.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "feat")
    (repo / "shared.txt").write_text("feat\n")
    _git(repo, "commit", "-qam", "feat")
    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main\n")
    (repo / "from_main.txt").write_text("main\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "other.md").write_text("main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves on")
    _git(repo, "checkout", "-q", "feat")
    assert _git(repo, "merge", "main", check=False).returncode != 0, "expected a conflict"
    (repo / "shared.txt").write_text("feat and main\n")
    _git(repo, "add", "shared.txt")
    run_cli(repo, "task", "add", "T1", "--globs", globs)
    assert A.claim(repo, "T1", no_worktree=True, agent=AGENT).ok


def test_paths_the_merge_brought_in_unchanged_are_not_listed(repo):
    _mid_merge(repo, "shared.txt")
    out = _hook(repo)
    assert "from_main.txt" not in out and "docs/other.md" not in out, out
    assert "not covered" not in out, out  # the one path of its own is leased


def test_the_resolved_path_is_still_judged(repo):
    _mid_merge(repo, "unrelated.py")
    out = _hook(repo)
    assert "shared.txt" in out and "not covered" in out, out
    assert "from_main.txt" not in out, out


def test_a_merge_in_a_linked_worktree_is_judged_the_same(repo, tmp_path):
    """Where the report came from: an item's own worktree, whose `.git` is a file and
    whose MERGE_HEAD lives under the common gitdir's `worktrees/<name>`."""
    run_cli(repo, "init")
    (repo / "shared.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    tree = tmp_path / "feat-tree"
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(tree))
    (tree / "shared.txt").write_text("feat\n")
    _git(tree, "commit", "-qam", "feat")
    (repo / "shared.txt").write_text("main\n")
    (repo / "from_main.txt").write_text("main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves on")
    assert _git(tree, "merge", "main", check=False).returncode != 0, "expected a conflict"
    (tree / "shared.txt").write_text("feat and main\n")
    _git(tree, "add", "shared.txt")
    run_cli(repo, "task", "add", "T1", "--globs", "shared.txt")
    assert A.claim(repo, "T1", no_worktree=True, agent=AGENT).ok
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "DDFLOW_AGENT": AGENT,
    }
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(tree), "hooks", "check-commit"],
        cwd=tree,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    out = p.stdout + p.stderr
    assert "from_main.txt" not in out and "not covered" not in out, out
