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


def test_the_warning_reaches_the_gate_evidence_and_no_item_adds_none(repo: Path):
    from ddflow.api import review as R
    from ddflow.api._base import _load

    _setup(repo)
    _log, cfg, st = _load(repo, "bob")
    said: list[str] = []
    ev = R._name_the_item(repo, cfg, st, "T1", "rubber_duck", "bob", said.append, {"keep": 1})
    assert ev == {"keep": 1, "reviewed_for_holder": "bob reviewed alice's T1"}
    assert R._name_the_item(repo, cfg, st, "T1", "rubber_duck", "alice", said.append, None) is None
    assert R._name_the_item(repo, cfg, st, "", "rubber_duck", "alice", said.append, None) is None


def test_an_item_with_no_live_lease_is_said_to_have_none(repo: Path):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "T9", "--phase", "P1", "--title", "t", "--new")
    assert "T9 has no live lease" in _review(repo, "bob", "T9")
