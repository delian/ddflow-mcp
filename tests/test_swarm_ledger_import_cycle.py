"""Importing the swarm ledger and the metrics package must work in a fresh interpreter, either order."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # this tree, first on sys.path
LEDGER = "ddflow.services.swarm.ledger"
RUNS = "ddflow.services.metrics.runs_to_merge"


@pytest.mark.parametrize("order", [(LEDGER, RUNS), (RUNS, LEDGER), (LEDGER,), (RUNS,)])
def test_fresh_interpreter_import_has_no_cycle(order):
    code = "; ".join(f"import {m}" for m in order)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-800:]
