"""The gates package imports stay sorted: an unsorted re-export block turned main's lint red
(Bci-ruff-check-ci-lint-28f149cf83)."""

import shutil
import subprocess
from pathlib import Path

import pytest

INIT = Path(__file__).resolve().parents[1] / "ddflow" / "services" / "gates" / "__init__.py"


def test_gates_package_imports_are_sorted() -> None:
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")
    p = subprocess.run([ruff, "check", "--select", "I", str(INIT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
