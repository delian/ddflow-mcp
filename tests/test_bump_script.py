"""`scripts/bump.sh` — the step publish.yml runs on every release.

The bug that motivated this file (Bf898a7f3b8): the script checks its own writes by
IMPORTING ddflow from the tree, and Python trusts `__pycache__` whenever the source's
size and whole-second mtime match. A patch bump keeps the size (0.1.2 -> 0.1.3), so two
bumps inside one second read the previous bytecode and failed "bump did not take:
SERVER_INFO is 0.1.2" with every file correctly written. Timing makes that flaky to
reproduce; bytecode in unchecked-hash mode, which Python never validates against its
source, is the same staleness made deterministic.
"""

from __future__ import annotations

import importlib.util
import json
import py_compile
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Stands in for `uv lock`, offline: record pyproject's version as the project's own.
_UV_STUB = """\
import pathlib, re, sys, tomllib
assert sys.argv[1:2] == ["lock"], sys.argv
new = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]
lock = pathlib.Path("uv.lock")
lock.write_text(re.sub(r'(name = "ddflow-mcp"\\nversion = )"[^"]*"', rf'\\g<1>"{new}"', lock.read_text()))
"""


def _checkout(tmp_path: Path) -> Path:
    """The files bump.sh touches or imports, in a repository of their own."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "ddflow", repo / "ddflow", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "bump.sh", repo / "scripts" / "bump.sh")
    for name in ("pyproject.toml", "server.json", "uv.lock"):
        shutil.copy(ROOT / name, repo / name)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _path_with_stubs(tmp_path: Path) -> str:
    """`python3` is THIS interpreter, so the bytecode planted below is the bytecode it
    reads; `uv` is the offline stub."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "python3").symlink_to(sys.executable)
    uv = bin_ / "uv"
    uv.write_text(f"#!{sys.executable}\n{_UV_STUB}")
    uv.chmod(0o755)
    return f"{bin_}:/usr/bin:/bin"


def _version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]


def test_a_bump_is_verified_against_the_source_it_wrote_not_stale_bytecode(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    repo = _checkout(tmp_path)
    before = _version(repo)
    # The bytecode a previous bump (or test run) left behind, for the version now in the
    # tree -- and marked never to be checked against the source.
    for mod in ("ddflow/__init__.py", "ddflow/surfaces/mcp.py"):
        src = repo / mod
        py_compile.compile(
            str(src),
            cfile=importlib.util.cache_from_source(str(src)),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )

    r = subprocess.run(
        ["sh", "scripts/bump.sh", "patch"],
        cwd=repo,
        env={"PATH": _path_with_stubs(tmp_path), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    major, minor, patch = (int(b) for b in before.split("."))
    want = f"{major}.{minor}.{patch + 1}"
    assert r.returncode == 0, f"bump.sh patch failed:\n{r.stdout}\n{r.stderr}"
    assert _version(repo) == want
    assert json.loads((repo / "server.json").read_text())["version"] == want
    assert f'__version__ = "{want}"' in (repo / "ddflow" / "__init__.py").read_text()
    assert re.search(rf'name = "ddflow-mcp"\nversion = "{re.escape(want)}"', (repo / "uv.lock").read_text())
