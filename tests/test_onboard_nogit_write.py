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


def test_a_repository_git_refuses_is_not_told_to_git_init(repo, monkeypatch):
    """A failed rev-parse on a directory that HAS a .git (dubious ownership, say) must not
    be answered with `git init`, whatever language git's message is in."""
    from ddflow.api import onboard as OB

    class Refused:
        ok = False
        err = "fatal: detected dubious ownership in repository"

    monkeypatch.setattr(OB.G, "run", lambda *a, **k: Refused())
    assert OB._not_a_repository(repo) is False


def test_a_translated_git_message_still_counts_as_not_a_repository(tmp_path, monkeypatch):
    from ddflow.api import onboard as OB

    class Refused:
        ok = False
        err = "fatal: kein Git-Repository (oder irgendeines der Elternverzeichnisse): .git"

    monkeypatch.setattr(OB.G, "run", lambda *a, **k: Refused())
    assert OB._not_a_repository(tmp_path) is True
