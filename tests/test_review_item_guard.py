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


def _setup_with_tree(repo: Path) -> Path:
    """`_setup`, returning the item's own worktree `claim` made for it (resolved absolute)."""
    _setup(repo)
    from ddflow.api._base import _load

    _log, _cfg, st = _load(repo, "alice")
    tree = Path(st.items["T1"].worktree)
    return tree if tree.is_absolute() else (repo / tree).resolve()


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
    assert len(said) == 1 and "leased to alice, and you are bob" in said[0]
    assert R._name_the_item(repo, cfg, st, "T1", "rubber_duck", "alice", said.append, None) is None
    assert R._name_the_item(repo, cfg, st, "", "rubber_duck", "alice", said.append, None) is None
    assert len(said) == 2 and "no item named" in said[1], "the holder's own review says nothing"


def test_an_item_with_no_live_lease_is_said_to_have_none(repo: Path):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "T9", "--phase", "P1", "--title", "t", "--new")
    assert "T9 has no live lease" in _review(repo, "bob", "T9")


def test_an_unleased_item_leaves_a_mark_in_the_evidence(repo: Path):
    from ddflow.api import review as R
    from ddflow.api._base import _load

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "T9", "--phase", "P1", "--title", "t", "--new")
    _log, cfg, st = _load(repo, "bob")
    ev = R._name_the_item(repo, cfg, st, "T9", "critic", "bob", lambda _l: None, None)
    assert ev == {"reviewed_unleased": True}


def test_review_from_the_items_own_tree_under_the_default_identity_does_not_warn(
    repo: Path, monkeypatch
):
    """B090201fcef: the agent working in the tree `claim` made under the DEFAULT (derived,
    not declared) identity is NOT another agent -- standing in that tree IS being its
    holder, so the review warns nothing (heartbeat already renews on the holder's behalf
    from that same tree). The DFFLOW_AGENT env is cleared so the default resolves as
    `derived`, not an env-declared identity."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    from ddflow.api import review as R
    from ddflow.api._base import _load
    from ddflow.config import Config

    tree = _setup_with_tree(repo)
    cfg = Config.load(repo)  # unbound: agent.id is its default, so "" resolves as "derived"
    _log, _cfg, st = _load(repo, "bob")
    said: list[str] = []
    ev = R._name_the_item(
        repo, cfg, st, "T1", "rubber_duck", "", said.append, None, called_from=tree
    )
    assert ev is None, ev  # no "reviewed_for_holder": this IS the holder's own tree
    assert not said, said
    # An EXPLICIT non-holder still warns, even from that same tree: it declared itself.
    said2: list[str] = []
    ev2 = R._name_the_item(
        repo, cfg, st, "T1", "rubber_duck", "bob", said2.append, None, called_from=tree
    )
    assert ev2 == {"reviewed_for_holder": "bob reviewed alice's T1"}, ev2
    assert any("leased to alice" in s for s in said2), said2
    # A non-holder declared through the ENVIRONMENT also still warns: `DDFLOW_AGENT` is a
    # declaration the caller is not the holder, so the item-own-tree read is not taken.
    monkeypatch.setenv("DDFLOW_AGENT", "agent-b")
    said3: list[str] = []
    ev3 = R._name_the_item(
        repo, cfg, st, "T1", "rubber_duck", "", said3.append, None, called_from=tree
    )
    assert ev3 == {"reviewed_for_holder": "agent-b reviewed alice's T1"}, ev3
    assert any("leased to alice" in s for s in said3), said3
