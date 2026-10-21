"""MCP `initialize` folds the event log once (B-mcp-initialize-folds-log).

Building the server instructions folded the whole log ~6 times (the queue counts, the
recoverable-trees scan, and each of the three upgrade-line readers loading the project on
its own): 6 s on this repo's log, a connect-timeout risk and the cost of every re-exec.
Fold cost scales with the log, so the guard counts folds rather than timing them.
"""

from __future__ import annotations

import sys

from conftest import run_cli

from ddflow.core import model


def _count_folds(monkeypatch) -> list[int]:
    """Count every call of `fold`, whichever module imported it by name."""
    real = model.fold
    calls = [0]

    def counting(*args, **kw):
        calls[0] += 1
        return real(*args, **kw)

    for mod in list(sys.modules.values()):
        if getattr(mod, "fold", None) is real:
            monkeypatch.setattr(mod, "fold", counting)
    return calls


def test_instructions_fold_the_log_once_per_initialize(repo, monkeypatch):
    from ddflow.surfaces.mcp import _instructions

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "one")
    _instructions(repo)  # the first handshake may stamp markers; measure the steady state
    calls = _count_folds(monkeypatch)
    text = _instructions(repo)
    assert "ddflow_brief" in text
    assert calls[0] <= 1, f"initialize folded the event log {calls[0]} times"


def test_queue_counts_see_an_append_made_after_the_shared_load(repo, monkeypatch):
    """An event landing between the shared load and the queue's own read is not missed."""
    from ddflow.api import surf_mcp
    from ddflow.config import Config

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "one")
    real_load = surf_mcp._load

    def load_then_append(r, agent=""):
        out = real_load(r, agent)
        run_cli(repo, "task", "add", "T2", "--title", "two")
        return out

    monkeypatch.setattr(surf_mcp, "_load", load_then_append)
    v: dict = {}
    with surf_mcp.load_scope():
        surf_mcp.fill_queue(v, repo, Config.load(repo), "")
    assert v["ready"] == 2, v


def test_every_handshake_reader_shares_one_project_load(repo, monkeypatch):
    """The queue counts and the three upgrade readers ask `_load` for the same project; inside
    one scope it loads (and folds) once, outside it each asks afresh."""
    from ddflow.api import _base, setup, surf_mcp
    from ddflow.config import Config

    run_cli(repo, "init")
    loads = [0]
    real = _base._load_fresh

    def counting(r, agent=""):
        loads[0] += 1
        return real(r, agent)

    monkeypatch.setattr(_base, "_load_fresh", counting)

    def readers() -> None:
        surf_mcp.fill_queue({}, repo, Config.load(repo), "")
        setup.upgrade_start(repo)
        setup.upgrade_notice(repo)
        setup.release_proposal(repo)

    with surf_mcp.load_scope():
        readers()
    assert loads[0] == 1, loads[0]
    loads[0] = 0
    readers()
    assert loads[0] > 1, "the unscoped path must stay fresh per reader"
