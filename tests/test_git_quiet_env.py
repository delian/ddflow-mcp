"""B4bb3c23c45: git's auto-maintenance is off for every git process the suite starts, so a
test that pushes into a bare remote and clones it cannot race `objects/maintenance.lock`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import _gitquiet


def _git_config(repo: Path, key: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", key], capture_output=True, text=True
    )
    return out.stdout.strip()


def test_a_suite_repository_has_no_auto_maintenance(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert _git_config(tmp_path, "gc.auto") == "0"
    assert _git_config(tmp_path, "maintenance.auto") == "false"
    assert _git_config(tmp_path, "receive.autogc") == "false"


def test_quiet_git_keeps_entries_the_environment_already_has() -> None:
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "x"}
    _gitquiet.quiet_git(env)
    assert env["GIT_CONFIG_KEY_0"] == "user.name" and env["GIT_CONFIG_VALUE_0"] == "x"
    assert env["GIT_CONFIG_COUNT"] == str(1 + len(_gitquiet.QUIET))
    again = dict(env)
    _gitquiet.quiet_git(again)
    assert again == env, "idempotent"


def test_quiet_git_tolerates_a_bad_count() -> None:
    env = {"GIT_CONFIG_COUNT": "many"}
    _gitquiet.quiet_git(env)
    assert env["GIT_CONFIG_COUNT"] == str(len(_gitquiet.QUIET))
