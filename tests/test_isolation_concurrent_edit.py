"""B-test-suite-guard-fires-on-concurrent-edit: an agent editing a tracked file in the worktree
while the unit_tests gate runs the suite there is not the suite modifying the real repository.

Under that gate only INDEX-side status lines are compared (a fixture's stray `git add`, an
intent-to-add ` A`); everywhere else, as before, every tracked-file line is."""

from __future__ import annotations

from types import SimpleNamespace

import _isolation as ISO


def _fake_git(porcelain: str):
    def run(*args: str):
        out = porcelain if args[:1] == ("status",) else ""
        return SimpleNamespace(stdout=out, returncode=0)

    return run


EDITS = " M tests/test_a.py\n M ddflow/x.py\n"


def test_under_the_unit_tests_gate_an_unstaged_edit_is_not_the_suites(monkeypatch):
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "unit_tests")
    monkeypatch.setattr(ISO, "_git", _fake_git(EDITS))
    assert ISO.snapshot()["status"] == []


def test_under_that_gate_staged_and_intent_to_add_lines_still_are(monkeypatch):
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "unit_tests")
    porcelain = "A  README.md\nM  docs/a.md\nMM ddflow/y.py\n A new.py\n M ddflow/z.py\n"
    monkeypatch.setattr(ISO, "_git", _fake_git(porcelain))
    assert ISO.snapshot()["status"] == [
        " A new.py",
        "A  README.md",
        "M  ddflow/y.py",
        "M  docs/a.md",
    ]


def test_anywhere_else_an_unstaged_edit_is_still_a_change(monkeypatch):
    for gate in (None, "ci"):
        if gate:
            monkeypatch.setenv("DDFLOW_RUNNING_GATE", gate)
        else:
            monkeypatch.delenv("DDFLOW_RUNNING_GATE", raising=False)
        monkeypatch.setattr(ISO, "_git", _fake_git(EDITS))
        assert ISO.snapshot()["status"] == sorted(EDITS.splitlines()), gate


def test_the_guard_stays_quiet_when_an_edit_appears_during_a_unit_tests_run(monkeypatch):
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "unit_tests")
    monkeypatch.setattr(ISO, "_git", _fake_git(""))
    before = ISO.snapshot()
    monkeypatch.setattr(ISO, "_git", _fake_git(" M tests/test_a.py\n"))
    assert ISO._changes(before, ISO.snapshot()) == []


def test_an_edit_to_an_already_staged_file_is_not_a_change_under_that_gate(monkeypatch):
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "unit_tests")
    monkeypatch.setattr(ISO, "_git", _fake_git("M  foo.py\n"))
    before = ISO.snapshot()
    monkeypatch.setattr(ISO, "_git", _fake_git("MM foo.py\n"))  # the agent edited it again
    assert ISO._changes(before, ISO.snapshot()) == []


def test_accepted_limit_an_unstaged_suite_write_is_not_caught_under_that_gate(monkeypatch):
    """Documented in `tracked_status`: there it cannot be told from the agent's own edit."""
    monkeypatch.setenv("DDFLOW_RUNNING_GATE", "unit_tests")
    monkeypatch.setattr(ISO, "_git", _fake_git(" M README.md\n"))
    assert ISO.snapshot()["status"] == []
