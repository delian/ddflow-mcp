"""`ddflow similar` accepts a `--floor` override (B76826fc2be), mirroring `ddflow dupes`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _similar(repo, *args):
    return run_cli(repo, "--json", "similar", *args)


def test_a_low_floor_surfaces_a_near_miss_the_default_floor_hid(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "dedupe.show_floor", "0.5")  # raise the default above the hit
    code, _o, err = run_cli(
        repo, "task", "add", "GV-complexity-score", "--title",
        "One deterministic complexity score per task",
    )  # fmt: skip
    assert code == 0, err
    q = "complexity score estimate"  # scores ~0.45: below the 0.5 floor just set
    code, _out, err = _similar(repo, q, "--kind", "task")
    assert code == 2, err  # hidden at the 0.5 floor
    code, out, err = _similar(repo, q, "--kind", "task", "--floor", "0")
    assert code == 0, err  # --floor 0 surfaces the near miss the default hid
    rows = json.loads(out)
    assert any(r["id"] == "GV-complexity-score" for r in rows), rows
