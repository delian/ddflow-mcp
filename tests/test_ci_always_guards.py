"""The repo-wide guards a selected `ddflow ci run` must always run (B7d4ab0b268): each broke main
past a selection that never reached it (a new tool parameter, a knob read through getattr, a knob
the upgrade manifest does not declare)."""

from __future__ import annotations

from pathlib import Path

from ddflow.config import Config
from ddflow.services import ci_scope

ROOT = Path(__file__).resolve().parents[1]

GUARDS = (
    "tests/test_declared_export.py",
    "tests/test_ratchet_no_dead_knobs.py",
    "tests/compat/test_surface.py",
    "tests/test_registry.py",  # hand-rolled register() idioms
    "tests/test_no_agent_literals.py",  # hard-coded agent names
    "tests/test_ci_always_guards.py",  # this file: deleting a flag must be caught too
)


def test_the_selection_always_runs_these_guards():
    ran = ci_scope.always_tests(Config(), ROOT)
    for guard in GUARDS:
        assert guard in ran, guard


ALWAYS = True
