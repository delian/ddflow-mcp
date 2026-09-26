"""Packaging is only testable by packaging. These tests build the real artefact.

The bug that motivated this file: `templates/` lived BESIDE the package rather than
inside it, so `ddflow adopt` worked perfectly from a source checkout and raised
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
    assert "ddflow/templates/drivers/implement-phase.md" in names, (
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
    assert "ddflow = ddflow.surfaces.cli:main" in entry
    assert "ddflow-mcp = ddflow.surfaces.mcp:main" in entry


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
        [str(venv / "bin" / "ddflow"), "adopt", "--agents", "claude"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"installed adopt failed:\n{r.stdout}\n{r.stderr}"
    assert (repo / "docs/ddflow/drivers/implement-phase.md").is_file()
    assert (repo / ".ddflow" / "config.toml").is_file()
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert "ddflow" in mcp["mcpServers"]


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
        [str(venv / "bin" / "ddflow-mcp")],
        cwd=str(repo),
        input=msgs,
        capture_output=True,
        text=True,
        timeout=120,
    )
    reply = json.loads(r.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "ddflow"


def test_the_declared_versions_agree():
    """pyproject, server.json and the server's own banner must not drift apart.

    A tag that disagrees with any of them publishes a version nobody can reproduce
    from the tree; the release workflow checks the same invariant.
    """
    sys.path.insert(0, str(ROOT))
    from ddflow.surfaces.mcp import SERVER_INFO

    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    srv = json.loads((ROOT / "server.json").read_text())
    assert proj == srv["version"] == SERVER_INFO["version"], (
        f"version drift: pyproject={proj} server.json={srv['version']} "
        f"SERVER_INFO={SERVER_INFO['version']}"
    )
    pypi = [k for k in srv["packages"] if k["registryType"] == "pypi"]
    assert len(pypi) == 1, f"expected exactly one pypi package, got {len(pypi)}"
    assert pypi[0]["version"] == proj
    assert pypi[0]["identifier"] == proj_name()

    # EVERY OCI identifier's tag must be the version. One left behind while `version`
    # moved on publishes a manifest pointing at the PREVIOUS image — discoverable in an
    # IDE marketplace, installable, and the wrong build. The release workflow and
    # `scripts/release.sh` check the same thing; this is the copy that runs on every
    # ordinary test run, which is the one that catches it before a tag exists.
    stale = [
        k["identifier"]
        for k in srv["packages"]
        if k["registryType"] == "oci" and not k["identifier"].endswith(f":{proj}")
    ]
    assert not stale, f"OCI identifiers not tagged {proj}: {stale}"


def proj_name() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["name"]


def test_the_manifest_offers_a_way_to_run_it_without_python():
    """The reason the OCI packages exist: an operator with no Python toolchain must be
    able to install this from a marketplace, not just from PyPI.

    Both registries are named because they serve different people — Docker Hub is what
    the README documents, ghcr.io needs no account beyond the repository's own.
    """
    srv = json.loads((ROOT / "server.json").read_text())
    oci = {k["identifier"].split("/")[0] for k in srv["packages"] if k["registryType"] == "oci"}
    assert {"docker.io", "ghcr.io"} <= oci, f"missing an OCI registry: {sorted(oci)}"
    for k in srv["packages"]:
        assert k["transport"]["type"] == "stdio", k


def test_the_manifest_does_not_describe_behaviour_the_code_does_not_have():
    """`server.json` is PUBLISHED — it is what a marketplace shows people, so a stale
    claim in it is a stale claim in front of every prospective user.

    It said `DDFLOW_AGENT` "defaults to host-pid", which `default_agent_id`'s own
    docstring records as the OLD behaviour, removed because `claim` and the commit hook
    seconds later resolved to different agents. Checked against the real default rather
    than against a remembered one.
    """
    sys.path.insert(0, str(ROOT))
    from ddflow.infra.log import default_agent_id

    srv = json.loads((ROOT / "server.json").read_text())
    described = {
        e["name"]: e["description"]
        for k in srv["packages"]
        for e in k.get("environmentVariables", [])
    }
    assert "DDFLOW_AGENT" in described and "DDFLOW_REPO" in described, sorted(described)

    actual = default_agent_id(ROOT)
    assert "-" in actual, actual
    assert "pid" not in described["DDFLOW_AGENT"].lower(), (
        "the manifest still describes the host-pid default, which was removed as a bug: "
        + described["DDFLOW_AGENT"]
    )
    # It must also say the thing that actually matters about it.
    assert "worktree" in described["DDFLOW_AGENT"].lower(), described["DDFLOW_AGENT"]


def test_the_release_script_is_dry_by_default():
    """A release script that publishes when run with no arguments is a release script
    someone will run to see what it does."""
    src = (ROOT / "scripts" / "release.sh").read_text()
    assert "PUBLISH=0" in src, "the default is not dry"
    assert "--publish" in src
    # The irreversible calls must all sit behind the flag.
    after = src[src.index('if [ "$PUBLISH" -eq 0 ]') :]
    before = src[: src.index('if [ "$PUBLISH" -eq 0 ]')]
    for irreversible in ("uv publish", "docker push", "mcp-publisher publish"):
        assert irreversible not in before, f"{irreversible!r} runs before the dry-run exit"
        assert irreversible in after, f"{irreversible!r} is not in the publish path at all"


# -- the spawn contract: a module path a move can silently invalidate ------------------


def test_every_module_path_ddflow_writes_into_a_config_actually_imports():
    """`adopt` writes `python -m <module>` into other people's MCP configs.

    A wrong module there fails in the worst possible place: the server spawns, cannot
    import, writes nothing to stdout, and the client blocks on a handshake that will
    never arrive. Nothing errors, nothing logs — the session just stops.

    That is exactly what happened when the package was split into layers:
    `ddflow.mcp_server` became `ddflow.surfaces.mcp` and the hardcoded string in
    `_launch_entry` did not follow. The full suite ran for two hours before it was
    killed. This asserts the string against the import system, which is the only thing
    that can tell the truth about it.
    """
    import importlib

    from ddflow.services.adopt import MCP_MODULE

    mod = importlib.import_module(MCP_MODULE)
    assert hasattr(mod, "main"), f"{MCP_MODULE} has no main() to spawn"


def test_the_pythonpath_adopt_writes_can_import_ddflow():
    """Counted paths (`parents[1]`) break when a file moves; derived ones do not."""
    import subprocess
    import sys as _sys

    from ddflow.services.adopt import MCP_MODULE, _package_parent

    parent = _package_parent()
    assert (Path(parent) / "ddflow" / "__init__.py").is_file(), (
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
