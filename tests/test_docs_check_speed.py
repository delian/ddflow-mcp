"""The docs check walks ddflow's own `--help` in process, fast and without a spawn
(DX-docs-check-speed). The in-process/spawn flag equivalence is pinned by
tests/test_docscheck_help_in_process.py; this pins the speed and the no-spawn contract."""

from __future__ import annotations

import time
from pathlib import Path

from ddflow.infra import proc as P
from ddflow.services import docscheck_help as H

ROOT = Path(__file__).resolve().parents[1]
#: The budget (seconds) a whole ddflow help walk may take; the spawning walk took ~4 minutes.
WALK_BUDGET_S = 10


def _all_chains():
    """Every verb chain of the real CLI registry (test-side enumeration of argparse)."""
    import argparse

    from ddflow.surfaces.cli import build_parser

    def walk(parser, prefix):
        for act in parser._actions:
            if isinstance(act, argparse._SubParsersAction):
                for name, sub in act.choices.items():
                    yield [*prefix, name]
                    yield from walk(sub, [*prefix, name])

    return list(walk(build_parser(), []))


def _no_spawn(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ddflow's own help must not spawn a process")

    monkeypatch.setattr(P, "capture", boom)


def test_the_ddflow_help_walk_is_in_process_and_under_ten_seconds(monkeypatch):
    chains = _all_chains()
    assert len(chains) > 100
    _no_spawn(monkeypatch)
    monkeypatch.setattr(H, "_parser", [None])  # cold: the registry is built inside the timing
    start = time.monotonic()  # construction is timed too: a walk may be eager in it
    idx = H.HelpIndex(H.command_runner(ROOT, 60), ["ddflow"])
    for chain in chains:
        resolved = idx.resolve(["ddflow", *chain])
        assert resolved is not None and resolved[0] == ["ddflow", *chain], chain
        assert resolved[1], chain
    assert time.monotonic() - start < WALK_BUDGET_S
    assert not idx.unavailable


def test_a_spawned_tool_still_spawns_under_the_timeout_knob(monkeypatch):
    seen = []

    def fake(argv, timeout=None, cwd=None):
        seen.append((argv, timeout))
        return P.Captured(0, "usage: other [--x]\n", "")

    monkeypatch.setattr(P, "capture", fake)
    out = H.command_runner(ROOT, 7)(["othertool", "--help"])
    assert out.returncode == 0 and seen == [(["othertool", "--help"], 7)]
