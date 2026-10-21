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
