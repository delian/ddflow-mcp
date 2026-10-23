"""B-test-suite-guard-fires-on-concurrent-edit: an agent editing a tracked file in the worktree
while the suite runs is not the suite modifying the real repository.

The leak the guard exists for stages something (a one-line README.md `git add`ed by a fixture)
or commits; an UNSTAGED edit is the working copy being worked on."""

from __future__ import annotations

from types import SimpleNamespace

import _isolation as ISO


def _fake_git(porcelain: str):
    def run(*args: str):
        if args[:2] == ("rev-parse", "--git-common-dir"):
            return SimpleNamespace(stdout="", returncode=0)
        if args[:1] == ("status",):
            return SimpleNamespace(stdout=porcelain, returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    return run


def test_an_unstaged_edit_by_someone_else_is_not_a_change_the_suite_made(monkeypatch):
    monkeypatch.setattr(ISO, "_git", _fake_git(" M tests/test_a.py\n M ddflow/x.py\n"))
    assert ISO.snapshot()["status"] == []


def test_a_staged_change_still_is(monkeypatch):
    monkeypatch.setattr(
        ISO, "_git", _fake_git("A  README.md\nM  docs/a.md\nMM ddflow/y.py\n M ddflow/z.py\n")
    )
    assert ISO.snapshot()["status"] == ["A  README.md", "M  docs/a.md", "MM ddflow/y.py"]


def test_the_guard_does_not_fire_when_only_an_edit_appeared_during_the_run(monkeypatch):
    monkeypatch.setattr(ISO, "_git", _fake_git(""))
    before = ISO.snapshot()
    monkeypatch.setattr(ISO, "_git", _fake_git(" M tests/test_a.py\n"))
    assert ISO._changes(before, ISO.snapshot()) == []
