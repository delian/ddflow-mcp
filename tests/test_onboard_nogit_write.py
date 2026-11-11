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


def test_a_directory_with_a_dot_git_is_not_told_to_git_init(tmp_path):
    """A `.git` that git may refuse to open (dubious ownership) is still a repository to
    report on, never an invitation to `git init`."""
    from ddflow.api import onboard as OB

    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    assert OB._not_a_repository(tmp_path / "sub") is False


def test_a_directory_without_a_dot_git_above_it_is_not_a_repository(tmp_path):
    from ddflow.api import onboard as OB

    assert OB._not_a_repository(tmp_path) is True


def test_a_bare_repository_is_a_repository(tmp_path):
    from ddflow.api import onboard as OB

    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "objects").mkdir()
    assert OB._not_a_repository(tmp_path) is False
