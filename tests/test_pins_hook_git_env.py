"""The post-merge pins hook never runs recipes inside a linked worktree (B683c75d7b3): its
GIT_DIR/GIT_INDEX_FILE made pytest fixtures commit "init" to the worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.api import pin_files
from ddflow.services import pins_merge as PM


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


def test_after_merge_hook_skips_a_linked_worktree(tmp_path: Path, monkeypatch) -> None:
    for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(k, raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "commit", "-q", "--allow-empty", "-m", "i")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "b")
    ran: list[Path] = []
    monkeypatch.setattr(PM, "after_merge", lambda repo, cfg: ran.append(repo) or "ran")
    head = subprocess.check_output(["git", "-C", str(wt), "rev-parse", "HEAD"])
    out = pin_files.pins_regenerate(main, after_merge=True, start=wt)
    assert ran == [] and out.ok
    assert subprocess.check_output(["git", "-C", str(wt), "rev-parse", "HEAD"]) == head
    pin_files.pins_regenerate(main, after_merge=True, start=main)
    assert ran == [main]
