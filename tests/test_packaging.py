"""Packaging is only testable by packaging. These tests build the real artefact.

The bug that motivated this file: `templates/` lived BESIDE the package rather than
inside it, so `orchard adopt` worked perfectly from a source checkout and raised
FileNotFoundError for every installed user. No amount of running from the tree can
catch that — the tree is exactly where it works.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    if not shutil.which("uv"):
        pytest.skip("uv is not installed; cannot build the distribution")
    out = tmp_path_factory.mktemp("dist")
    r = subprocess.run(
        ["uv", "build", "--project", str(ROOT), "--out-dir", str(out)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, f"uv build failed:\n{r.stdout}\n{r.stderr}"
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


def test_the_wheel_contains_the_driver_templates(wheel):
    """`adopt` copies these at runtime; absent, every installed user gets a traceback."""
    names = zipfile.ZipFile(wheel).namelist()
    assert "orchard/templates/drivers/implement-phase.md" in names, (
        "the canonical driver is missing from the wheel — adopt will fail for every "
        f"installed user. Wheel contains: {sorted(n for n in names if 'templ' in n)}"
    )
    deltas = [n for n in names if "templates/drivers/deltas/" in n]
    assert len(deltas) >= 5, f"per-agent deltas missing from the wheel: {deltas}"


def test_the_wheel_declares_both_entry_points(wheel):
    entry = (
        zipfile.ZipFile(wheel)
        .read(next(n for n in zipfile.ZipFile(wheel).namelist() if n.endswith("entry_points.txt")))
        .decode()
    )
    assert "orchard = orchard.surfaces.cli:main" in entry
    assert "orchard-mcp = orchard.surfaces.mcp:main" in entry


def test_the_package_has_no_runtime_dependencies(wheel):
    """Zero dependencies is the property that makes this installable everywhere an
    agent runs, including sandboxes with no reachable package index."""
    meta = (
        zipfile.ZipFile(wheel)
        .read(next(n for n in zipfile.ZipFile(wheel).namelist() if n.endswith("METADATA")))
        .decode()
    )
    requires = [
        ln for ln in meta.splitlines() if ln.startswith("Requires-Dist:") and "extra ==" not in ln
    ]
    assert not requires, f"a runtime dependency crept in: {requires}"


def test_installed_adopt_works_end_to_end(wheel, tmp_path):
    """Install the wheel into a clean venv and adopt a fresh repo — the real path."""
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    pip = venv / "bin" / "pip"
    subprocess.run([str(pip), "-q", "install", str(wheel)], check=True, timeout=600)

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "i"], check=True)

    r = subprocess.run(
        [str(venv / "bin" / "orchard"), "adopt", "--agents", "claude"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"installed adopt failed:\n{r.stdout}\n{r.stderr}"
    assert (repo / "docs/orchard/drivers/implement-phase.md").is_file()
    assert (repo / ".orchard" / "config.toml").is_file()
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert "orchard" in mcp["mcpServers"]


def test_installed_mcp_server_completes_a_handshake(wheel, tmp_path):
    venv = tmp_path / "venv2"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    subprocess.run(
        [str(venv / "bin" / "pip"), "-q", "install", str(wheel)], check=True, timeout=600
    )
    repo = tmp_path / "p2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    msgs = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        + "\n"
    )
    r = subprocess.run(
        [str(venv / "bin" / "orchard-mcp")],
        cwd=str(repo),
        input=msgs,
        capture_output=True,
        text=True,
        timeout=120,
    )
    reply = json.loads(r.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "orchard"


def test_the_declared_versions_agree():
    """pyproject, server.json and the server's own banner must not drift apart.

    A tag that disagrees with any of them publishes a version nobody can reproduce
    from the tree; the release workflow checks the same invariant.
    """
    sys.path.insert(0, str(ROOT))
    from orchard.surfaces.mcp import SERVER_INFO

    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    srv = json.loads((ROOT / "server.json").read_text())
    assert proj == srv["version"] == SERVER_INFO["version"], (
        f"version drift: pyproject={proj} server.json={srv['version']} "
        f"SERVER_INFO={SERVER_INFO['version']}"
    )
    assert srv["packages"][0]["version"] == proj
    assert (
        srv["packages"][0]["identifier"]
        == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]
    )


# -- the spawn contract: a module path a move can silently invalidate ------------------


def test_every_module_path_orchard_writes_into_a_config_actually_imports():
    """`adopt` writes `python -m <module>` into other people's MCP configs.

    A wrong module there fails in the worst possible place: the server spawns, cannot
    import, writes nothing to stdout, and the client blocks on a handshake that will
    never arrive. Nothing errors, nothing logs — the session just stops.

    That is exactly what happened when the package was split into layers:
    `orchard.mcp_server` became `orchard.surfaces.mcp` and the hardcoded string in
    `_launch_entry` did not follow. The full suite ran for two hours before it was
    killed. This asserts the string against the import system, which is the only thing
    that can tell the truth about it.
    """
    import importlib

    from orchard.services.adopt import MCP_MODULE

    mod = importlib.import_module(MCP_MODULE)
    assert hasattr(mod, "main"), f"{MCP_MODULE} has no main() to spawn"


def test_the_pythonpath_adopt_writes_can_import_orchard():
    """Counted paths (`parents[1]`) break when a file moves; derived ones do not."""
    import subprocess
    import sys as _sys

    from orchard.services.adopt import MCP_MODULE, _package_parent

    parent = _package_parent()
    assert (Path(parent) / "orchard" / "__init__.py").is_file(), (
        f"{parent} is not the directory containing the package"
    )
    # Prove it end to end: a clean interpreter with ONLY that on the path.
    p = subprocess.run(
        [_sys.executable, "-c", f"import {MCP_MODULE}; print('ok')"],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": parent, "PATH": os.environ.get("PATH", "")},
    )
    assert p.returncode == 0 and "ok" in p.stdout, (
        f"an agent spawning the server with this PYTHONPATH would hang:\n{p.stderr[-600:]}"
    )


def test_the_console_entry_points_resolve():
    """`pyproject` names two entry points; both must be importable attributes."""
    import importlib
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts, "the package must ship its console scripts"
    for name, target in scripts.items():
        mod_name, _, attr = target.partition(":")
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, attr), f"entry point {name} = {target} does not resolve"
