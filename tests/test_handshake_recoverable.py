"""The handshake's "Waiting for you right now" count (B904edd649c).

It counted every situation `leases.scan` returned -- one per ITEM, including trees
`recover` itself measured clean -- and the template called each one "work that exists
nowhere else". One harness tree adopted by 64 finished items read as 64 crashed agents to
salvage, every session. The count is now trees that may hold work, as `recover` and the
brief judge it.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.services import leases as L
from ddflow.surfaces.mcp import _instruction_vars, _instructions

HARNESS = "/x/.claude/worktrees/bridge-cse_1"


def _rec(item: str, worktree: str, salvageable: bool | None, **kw) -> L.Recovery:
    return L.Recovery(
        item=item,
        holder="a",
        kind="orphan_worktree",
        worktree=worktree,
        salvageable=salvageable,
        **kw,
    )


@pytest.fixture
def adopted(repo):
    run_cli(repo, "init")
    return repo


def _scan_returns(monkeypatch, recs):
    monkeypatch.setattr(L, "scan", lambda *a, **k: list(recs))


def test_clean_trees_are_not_announced_as_work_to_salvage(adopted, monkeypatch):
    _scan_returns(
        monkeypatch,
        [_rec(f"T{i}", HARNESS, False, adopted=True) for i in range(64)],
    )
    assert _instruction_vars(adopted)["recoverable"] == 0
    assert "Waiting for you right now" not in _instructions(adopted)


def test_the_count_is_per_tree_and_only_trees_that_may_hold_work(adopted, monkeypatch):
    _scan_returns(
        monkeypatch,
        [
            _rec("A", "/w/a", True, dirty_files=2),
            _rec("A2", "/w/a", True, dirty_files=2),  # same tree, a second item
            _rec("B", "/w/b", True, unmerged_commits=1),
            _rec("C", "/w/c", False),  # measured clean
        ],
    )
    assert _instruction_vars(adopted)["recoverable"] == 2
    text = _instructions(adopted)
    assert "Waiting for you right now" in text
    assert "2 worktree(s)" in text
