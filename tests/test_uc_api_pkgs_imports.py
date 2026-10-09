"""B-uc-api-pkgs: the api subpackages import their siblings at module level.

The function-level imports that hid import cycles were hoisted, so a cycle would now
show as an ImportError the moment a module is the FIRST thing imported (which one comes
first depends on the entry point: the CLI, the MCP server, a test). Each module of the
three packages is imported first, alone, in a fresh interpreter.
"""

from __future__ import annotations

import ast
import pkgutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

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


def _import_alone(name: str) -> str:
    r = subprocess.run(
        [sys.executable, "-c", f"import {name}"], capture_output=True, text=True, timeout=120
    )
    return r.stderr.strip().splitlines()[-1] if r.returncode else ""


def test_every_module_imports_first_without_a_cycle():
    with ThreadPoolExecutor(8) as ex:
        results = dict(zip(MODULES, ex.map(_import_alone, MODULES), strict=True))
    assert {m: e for m, e in results.items() if e} == {}


@pytest.mark.parametrize("pkg", PACKAGES, ids=lambda p: p.__name__)
def test_no_function_level_import_is_left(pkg):
    """The deferred_imports baselines of these packages are deleted (zero): a new
    function-level import fails tests/test_architecture_guards.py with the site named;
    this names the modules directly."""
    left = []
    for path in Path(pkg.__path__[0]).glob("*.py"):
        for fn in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                left += [
                    f"{path.name}:{n.lineno}"
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Import | ast.ImportFrom)
                ]
    assert left == []
