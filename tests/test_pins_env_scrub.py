"""A pin recipe run by the post-merge hook does not inherit git's repository variables
(B683c75d7b3, B36c484af97): the hook's GIT_DIR/GIT_INDEX_FILE made the tests the recipe runs
commit "init" to the worktree and stage a stray README.md."""

from __future__ import annotations

import sys
from pathlib import Path

from ddflow.services import pins as PINS


def test_a_recipe_command_sees_no_git_repository_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/real/.git")
    monkeypatch.setenv("GIT_INDEX_FILE", "/real/.git/index")
    monkeypatch.setenv("GIT_WORK_TREE", "/real")
    code = "import os; print([os.environ.get(k) for k in ('GIT_DIR','GIT_INDEX_FILE','GIT_WORK_TREE')])"
    r = PINS.Recipe("p", ("x",), (sys.executable, "-c", code), (sys.executable, "-c", code))
    out = PINS._run(tmp_path, r.check, r, 60, write=False)
    assert out.stdout.strip() == "[None, None, None]", out.stdout + out.stderr
    out = PINS._run(tmp_path, r.regen, r, 60, write=True)
    assert out.stdout.strip() == "[None, None, None]"
