"""B-uc-api-pkgs: the api subpackages import their siblings at module level (no function-level
import is left: tests/test_architecture_guards.py holds that at zero).

The function-level imports that hid import cycles were hoisted, so a cycle would now
show as an ImportError the moment a module is the FIRST thing imported (which one comes
first depends on the entry point: the CLI, the MCP server, a test). Each module of the
three packages is imported first, alone, in a fresh interpreter.
"""

from __future__ import annotations

import os
import pkgutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ddflow.api.knowledge
import ddflow.api.lifecycle
import ddflow.api.reporting

#: What this checks, read by `ddflow tests --item` (B2a1eaa259e).
GOVERNS = ("ddflow/api/lifecycle/**", "ddflow/api/reporting/**", "ddflow/api/knowledge/**")

PACKAGES = (ddflow.api.lifecycle, ddflow.api.reporting, ddflow.api.knowledge)
MODULES = sorted(
    [p.__name__ for p in PACKAGES]
    + [m.name for p in PACKAGES for m in pkgutil.iter_modules(p.__path__, p.__name__ + ".")]
)


ROOT = Path(__file__).resolve().parents[1]


def _import_alone(name: str) -> str:
    """ "" when ``name`` imports first, alone, from THIS tree; else the last line it printed."""
    r = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    if not r.returncode:
        return ""
    lines = r.stderr.strip().splitlines()
    return lines[-1] if lines else f"exited {r.returncode} with no stderr"


def test_every_module_imports_first_without_a_cycle():
    with ThreadPoolExecutor(8) as ex:
        results = dict(zip(MODULES, ex.map(_import_alone, MODULES), strict=True))
    assert {m: e for m, e in results.items() if e} == {}
