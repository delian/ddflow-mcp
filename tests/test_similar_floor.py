"""`ddflow similar` accepts a `--floor` override (B76826fc2be), mirroring `ddflow dupes`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _similar(repo, *args):
    return run_cli(repo, "--json", "similar", *args)


def test_a_low_floor_surfaces_a_near_miss_below_the_default(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(
        repo, "task", "add", "GV-complexity-score", "--title",
        "One deterministic complexity score per task",
    )  # fmt: skip
    assert code == 0, err
    q = "complexity score estimate"  # scores ~0.45: below the 0.5 the project sets
    code, out, err = _similar(repo, q, "--kind", "task")
    assert code == 0, err
    assert any(r["id"] == "GV-complexity-score" for r in json.loads(out))
    # the flag is the knob: a high floor hides it, a low floor honors being lowered
    code, _out, _err = _similar(repo, q, "--kind", "task", "--floor", "0.5")
    assert code == 2
    code, out, err = _similar(repo, q, "--kind", "task", "--floor", "0")
    assert code == 0, err
    assert any(r["id"] == "GV-complexity-score" for r in json.loads(out))
