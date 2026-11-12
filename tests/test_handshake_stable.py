"""The MCP initialize handshake is byte-stable across per-session state
(B-mcp-instructions-volatile-prefix).

The initialize instructions are injected into the model's context on connect. When the
per-session state --- the recoverable-tree count, the queue-loop count, rules drift,
companion states and gate gaps --- renders INSIDE them, two lanes started minutes apart
get a different prefix and the cross-lane prefix cache is lost. Those lines moved to the
brief (`ddflow/services/brief_volatile.py`); the handshake renders none of them, so the
injected bytes are the same whatever the state says.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.services import leases as L
from ddflow.surfaces.mcp import _instructions


@pytest.fixture
def adopted(repo):
    run_cli(repo, "init")
    return repo


def _rec(item: str, worktree: str, salvageable: bool | None, **kw) -> L.Recovery:
    return L.Recovery(
        item=item,
        holder="a",
        kind="orphan_worktree",
        worktree=worktree,
        salvageable=salvageable,
        **kw,
    )


def test_the_handshake_is_byte_stable_across_session_state(adopted, monkeypatch):
    monkeypatch.setattr(L, "scan", lambda *a, **k: [_rec("A", "/w/a", True, dirty_files=1)])
    first = _instructions(adopted)
    # The SAME handshake, a minute later, with a different recoverable set: the bytes the
    # model injects must not move, or two lanes no longer share a prefix cache.
    monkeypatch.setattr(
        L,
        "scan",
        lambda *a, **k: [_rec("A", "/w/a", True, dirty_files=1), _rec("B", "/w/b", True)],
    )
    second = _instructions(adopted)
    assert first == second, "the handshake changed with the per-session state"


def test_the_handshake_carries_no_digit_counted_state_line(adopted, monkeypatch):
    monkeypatch.setattr(
        L,
        "scan",
        lambda *a, **k: [_rec("A", "/w/a", True, dirty_files=2), _rec("B", "/w/b", True)],
    )
    text = _instructions(adopted)
    for line in (
        "crashed worktree(s)",
        "queue loop finding(s)",
        "Companions not wired up",
        "Gates with no tool behind them",
    ):
        assert line not in text, f"the handshake still renders volatile state: {line!r}"
