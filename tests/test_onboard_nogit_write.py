"""`ddflow onboard status` in a directory that is not a git repository.

A read-only status verb writes no project state and says what to do (`git init`), not a
raw GitError from a probe it ran underneath. The command log is the CLI's own record of
every command, best-effort, and is not project state.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _state(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.name != "cmdlog.jsonl"
    }


@pytest.mark.parametrize("stage", ["status", "preflight", "verify"])
def test_onboard_in_a_non_git_directory_writes_nothing_and_says_git_init(tmp_path, stage):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out, err = run_cli(plain, "onboard", stage)
    text = out + err
    assert code == 1, text
    assert "git init" in text, text
    assert "GitError" not in text, text
    assert _state(plain) == set(), _state(plain)
