"""The end-to-end scenarios, run by pytest.

These have found most of the real bugs in this project and the unit tests found few of
them: the composed MCP run alone found eight that 213 unit tests and four other
scenarios missed, and `full-lifecycle` found four CLI/MCP divergences before it passed
once. They live in `demos/` and were invoked by hand, which meant **the suite that
matters most was the one automation did not run**.

Marked `slow` and deselected by default, because each spawns real processes, creates
real git worktrees and runs a real `pytest` inside them — two to four minutes for the
set. Run them explicitly:

    pytest tests/test_scenarios.py -m slow        # or: python demos/run_all.py

The marker is the honest trade: putting four minutes of subprocess work into every
`pytest` run is how a suite becomes something people skip, and a skipped suite protects
nothing. What this file buys is that CI *can* run them by name, and that a scenario
which stops importing fails here rather than silently rotting until someone runs the
demos by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "demos"), str(ROOT)]


def _scenarios():
    import run_all

    return run_all.SCENARIOS


def test_every_scenario_still_imports():
    """Cheap, and it is the check that actually rots.

    A scenario is a module that reaches deep into the package — `ddflow_claim` tool
    names, `Plan` fields, event kinds. When the package moves, a scenario breaks at
    IMPORT time, and nobody finds out until the next manual run. This is that check,
    and it costs a second.
    """
    names = [n for n, _fn in _scenarios()]
    assert len(names) >= 6, names
    assert "full-lifecycle" in names and "mcp-orchestration" in names


@pytest.mark.slow
@pytest.mark.parametrize("name", [n for n, _ in _scenarios()], ids=lambda n: n)
def test_scenario(name, tmp_path):
    """One scenario, against a freshly invented project in a temp directory."""
    from harness import Fail, Scenario

    fn = dict(_scenarios())[name]
    sc = Scenario(name, tmp_path / name)
    try:
        fn(sc)
    except Fail as exc:
        pytest.fail(f"{name}: {exc}")
    assert sc.checks > 0, f"{name} asserted nothing — a scenario that checks nothing passes"
