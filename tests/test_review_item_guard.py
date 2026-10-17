"""A review says which item it is recorded against, and whose it is (DX-review-item-from-cwd).

A review launched from a backgrounded shell chain once recorded a failed gate on another
agent's item with nothing to say so."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _setup(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    code, _o, err = run_cli(
        repo, "--agent", "alice", "task", "add", "T1", "--phase", "P1", "--title", "t", "--new"
    )
    assert code == 0, err
    code, _o, err = run_cli(repo, "--agent", "alice", "claim", "T1")
    assert code == 0, err


def _review(repo: Path, who: str, *args: str) -> str:
    # A reviewer that does not exist, so no model is called: the run says what it is about
    # and records UNAVAILABLE, which is all the warning needs.
    _code, out, err = run_cli(repo, "--agent", who, "review", *args, "--gate", "rubber_duck")
    return out + err


def test_a_review_of_another_agents_item_warns_loudly(repo: Path):
    _setup(repo)
    said = _review(repo, "bob", "T1")
    assert "WARNING: T1 is leased to alice, and you are bob" in said, said


def test_the_holders_own_review_does_not_warn(repo: Path):
    _setup(repo)
    assert "WARNING" not in _review(repo, "alice", "T1")


def test_a_review_with_no_item_says_nothing_is_recorded(repo: Path):
    _setup(repo)
    said = _review(repo, "alice")
    assert "no item named" in said and "recording nothing" in said, said
