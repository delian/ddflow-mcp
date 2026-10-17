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


def test_recipe_child_with_hook_git_env_leaves_the_scratch_repo_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    from ddflow.services import pins as PINS

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _git(scratch, "init", "-q")
    _git(scratch, "commit", "-q", "--allow-empty", "-m", "i")
    before = subprocess.check_output(["git", "-C", str(scratch), "reflog"])
    monkeypatch.setenv("GIT_DIR", str(scratch / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(scratch / ".git" / "index"))
    work = tmp_path / "work"
    work.mkdir()
    code = (
        "import subprocess as s;"
        "s.run(['git','init','-q']);"
        "s.run(['git','-c','user.name=t','-c','user.email=t@t','commit','-q','--allow-empty','-m','init'])"
    )
    r = PINS.Recipe("p", ("x",), (sys.executable, "-c", code), (sys.executable, "-c", code))
    PINS._run(work, r.regen, r, 60, write=True)
    assert subprocess.check_output(["git", "-C", str(scratch), "reflog"]) == before
    assert (work / ".git").is_dir()
