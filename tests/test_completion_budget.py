"""Completion latency budgets: building the command tree off the real parser, and the id
lookup in a fresh TAB process, both stay inside finite, load-scaled ceilings so a TAB can
never hang (PRINCIPLE: bounded-everything)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from _wait import scaled  # noqa: E402

from ddflow.services.completion import tree as T  # noqa: E402
from ddflow.surfaces.cli import build_parser  # noqa: E402


def test_command_completion_meets_the_latency_budget():
    """Building the tree off the real parser is the per-TAB command-completion cost; it
    must stay well under a finite, load-scaled ceiling (best-of-three, so scheduling
    noise never reads as a budget miss)."""
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        tree = T.command_tree(build_parser())
        best = min(best, time.perf_counter() - start)
        assert T.at(tree, ("gate", "record")) is not None  # the real parser, at scale
    assert best < scaled(5.0)


def _index(d: Path) -> Path:
    (d / ".ddflow").mkdir(parents=True, exist_ok=True)
    p = d / ".ddflow" / "index.db-aaaaaaaa"
    con = sqlite3.connect(p)
    con.executescript("create table items(id text, kind text, title text, state text);")
    con.executemany(
        "insert into items values(?,?,?,?)",
        [("COMP-a", "task", "t", "open"), ("COMP-b", "task", "t", "done")],
    )
    con.commit()
    con.close()
    return p


def test_id_completion_meets_the_latency_budget(tmp_path):
    """A fresh id TAB (import the light entry point + query) completes under a finite
    ceiling; the startup import cost is part of what a TAB pays. The child is bounded by
    both a wall timeout and a closed stdin, and timed best-of-three so a single steal on
    an otherwise correct reply reads as noise, not a budget miss."""
    _index(tmp_path)
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "ddflow.completion_fast", "ids", "COMP"],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=scaled(60.0),
            check=False,
        )
        best = min(best, time.perf_counter() - start)
        assert result.returncode == 0
        assert result.stdout.split() == ["COMP-a", "COMP-b"], result.stderr
    assert best < scaled(2.0)
