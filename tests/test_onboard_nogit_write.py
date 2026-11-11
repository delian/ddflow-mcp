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
    out = set()
    for p in root.rglob("*"):
        if not p.is_file() or p.name == "cmdlog.jsonl":
            continue
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue  # git's own files, not ddflow state
        out.add(str(rel))
    return out


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


def test_onboard_status_in_a_git_dir_never_adopted_writes_no_local_state(tmp_path):
    """B a git repository ddflow was never adopted into still reports (and is not told to
    `git init`), but the read-only status must not leave `.ddflow/local` state behind: its
    `brief` probe runs `brief` underneath, which would create ticks, clone-id and flow
    samples."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    code, out, err = run_cli(repo, "onboard", "status")
    text = out + err
    assert code == 1, text
    assert "git init" not in text, text
    assert _state(repo) == set(), _state(repo)


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
    (tmp_path / "refs").mkdir()
    assert OB._not_a_repository(tmp_path) is False


def test_a_directory_with_only_head_and_objects_is_not_a_repository(tmp_path):
    from ddflow.api import onboard as OB

    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "objects").mkdir()
    assert OB._not_a_repository(tmp_path) is True
