"""The full-lifecycle scenario was split into modules (r-small-files): it is still found by
the same name and its acts still run in the same order."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
sys.path[:0] = [str(DEMOS), str(ROOT)]

PARTS = (
    "scenario_full_lifecycle",
    "lifecycle_data",
    "lifecycle_support",
    "lifecycle_acts_build",
    "lifecycle_acts_change",
    "lifecycle_acts_finish",
)


def test_scenario_is_still_discovered_under_the_same_name():
    import run_all

    assert "full-lifecycle" in [name for name, _fn in run_all.SCENARIOS]


def test_acts_run_in_the_same_order():
    tree = ast.parse((DEMOS / "scenario_full_lifecycle.py").read_text("utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    found = [
        c
        for c in ast.walk(run)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id.startswith("act")
    ]
    calls = [c.func.id for c in sorted(found, key=lambda c: (c.lineno, c.col_offset))]
    assert calls == [
        "act1_the_operator_speaks",
        "act2_phase_one_in_parallel",
        "act3_subtasks_and_a_bug",
        "act4_close_the_phase",
        "act5_requirements_change_mid_flight",
        "act6_the_guardrails",
        "act7_the_operator_asks",
        "act8_rebuild_from_the_log",
    ]


def test_every_part_is_under_the_size_limit():
    for part in PARTS:
        size = (DEMOS / f"{part}.py").stat().st_size
        assert size < 30000, f"{part}.py is {size} bytes"
