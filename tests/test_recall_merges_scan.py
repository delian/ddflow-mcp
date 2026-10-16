"""Bfaef48ced6: the recall replay scans main's merges from a feature branch too."""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_testselect_recall import merge_log


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def test_a_branch_that_merged_main_still_sees_mains_merges(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "feat")
    _git(repo, "checkout", "-q", "-b", "item")
    (repo / "b").write_text("2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "item work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-qm", "merge item: work", "item")
    landed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "feat")
    (repo / "c").write_text("3")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature work")
    _git(repo, "merge", "--no-ff", "-qm", "Merge branch 'main' into feat", "main")
    assert merge_log(repo) == [landed], "main's merge is on the second parent of this branch"
