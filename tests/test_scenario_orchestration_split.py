"""The mcp-orchestration scenario was split into modules (r-small-files): it is still found by
the same name, still exposes `run`, and its data and helper still resolve from the new module."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
sys.path[:0] = [str(DEMOS), str(ROOT)]

PARTS = ("scenario_mcp_orchestration", "orchestration_data")


def test_scenario_is_still_discovered_under_the_same_name():
    import run_all

    assert "mcp-orchestration" in [name for name, _fn in run_all.SCENARIOS]


def test_the_scenario_module_still_exposes_run_and_its_data():
    import orchestration_data
    import scenario_mcp_orchestration as sc

    assert callable(sc.run)
    for name in ("SCAFFOLD", "PARSE_PY", "PARSE_TEST", "STATS_PY", "STATS_TEST", "CLI_PY"):
        assert getattr(sc, name) is getattr(orchestration_data, name)
    assert sc._commit_in is orchestration_data._commit_in


def test_every_part_is_under_the_size_limit():
    for part in PARTS:
        size = (DEMOS / f"{part}.py").stat().st_size
        assert size < 30000, f"{part}.py is {size} bytes"
