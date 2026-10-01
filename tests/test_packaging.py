"""Packaging is only testable by packaging. These tests build the real artefact.

The bug that motivated this file: `templates/` lived BESIDE the package rather than
inside it, so `ddflow adopt` worked perfectly from a source checkout and raised
FileNotFoundError for every installed user. No amount of running from the tree can
catch that — the tree is exactly where it works.
"""

from __future__ import annotations

import json
import os
import re
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


#: Every runtime dependency, by distribution name. An ALLOWLIST, not a count.
#:
#: This was `assert not requires` — zero dependencies, the property that makes ddflow
#: installable in sandboxes with no reachable package index. That is still the design,
#: and Jinja2 is the one deliberate exception: prompts ARE Jinja templates, and for as
#: long as it was undeclared the package shipped a second, untested renderer that every
#: user ran and no developer did. 0.1.1's MCP handshake was broken for every unadopted
#: repository as a result (docs/BACKLOG.md B150-B154).
#:
#: Kept as a ratchet rather than deleted, because the original point stands: a
#: dependency must be a decision somebody made, not something that crept in with an
#: import. Adding a name here should feel like this comment.
ALLOWED_RUNTIME_DEPS = {"jinja2"}


def test_the_package_declares_only_the_dependencies_we_chose(wheel):
    """No dependency arrives by accident.

    Asserts on the built WHEEL's metadata rather than on pyproject.toml, because that
    is what a user actually installs and the two have disagreed before.
    """
    meta = (
        zipfile.ZipFile(wheel)
        .read(next(n for n in zipfile.ZipFile(wheel).namelist() if n.endswith("METADATA")))
        .decode()
    )
    requires = [
        ln for ln in meta.splitlines() if ln.startswith("Requires-Dist:") and "extra ==" not in ln
    ]
    # `Requires-Dist: jinja2>=3.1.6` -> `jinja2`. Normalised the way PyPI does, so
    # `Jinja2` and `jinja2` are one name rather than two.
    names = {
        re.split(r"[<>=!~;\[\s]", ln.split(":", 1)[1].strip(), maxsplit=1)[0]
        .lower()
        .replace("_", "-")
        for ln in requires
    }
    unexpected = names - ALLOWED_RUNTIME_DEPS
    assert not unexpected, (
        f"undeclared runtime dependency: {sorted(unexpected)}. Every dependency is a "
        f"decision — add it to ALLOWED_RUNTIME_DEPS with the reason, or remove the import."
    )


def test_the_declared_dependency_is_actually_installed_by_the_wheel(wheel, tmp_path):
    """The reason the dependency exists, proven rather than assumed.

    Jinja2 is declared so that the engine the templates are WRITTEN for is the engine
    that RUNS. A declaration nothing checks is how the two came apart in the first
    place: `python -m pytest` used the developer's ambient Jinja2 and `uv run pytest`
    used the fallback, on the same commit, with different results.
    """
    venv = tmp_path / "depvenv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    subprocess.run(
        [str(venv / "bin" / "pip"), "-q", "install", str(wheel)], check=True, timeout=600
    )
    got = subprocess.run(
        [str(venv / "bin" / "python"), "-c", "import jinja2; print(jinja2.__version__)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert got.returncode == 0, (
        f"installing the wheel did not bring Jinja2 with it: {got.stderr.strip()}"
    )


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
    import ddflow
    from ddflow.surfaces.mcp import SERVER_INFO

    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    srv = json.loads((ROOT / "server.json").read_text())
    # `ddflow.__version__` too: it is what `ddflow --version` prints, and it sat at 0.1.0
    # through the 0.1.1 release because neither this test nor `scripts/bump.sh` knew it
    # existed -- the sixth place, found when CI's `ddflow --version` step was fixed.
    assert proj == srv["version"] == SERVER_INFO["version"] == ddflow.__version__, (
        f"version drift: pyproject={proj} server.json={srv['version']} "
        f"SERVER_INFO={SERVER_INFO['version']} ddflow.__version__={ddflow.__version__}"
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


def test_ddflow_version_runs_and_prints_the_declared_version():
    """CI's build step runs `python -m ddflow --version` against the built wheel, and it
    exited 2 ("the following arguments are required: cmd") on every run since the step
    was written: the CLI had no `--version`, and the subcommand is required. Run exactly
    that command here, so the first place it fails is not a CI job."""
    import subprocess

    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    r = subprocess.run(
        [sys.executable, "-m", "ddflow", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"ddflow {proj}", r.stdout


def test_server_json_fits_the_registry_schema_limits():
    """server.json within the MCP Registry schema's string limits.

    The registry validates on publish, not before: a 251-char `description` passed every
    local check and failed publish #34 with 422 ("expected length <= 100") AFTER PyPI and
    the image had shipped, so 0.1.3 reached two registries and not the third, untagged.
    Limits from https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
    (ServerDetail); pinned here because a test must not fetch the schema from the network.
    """
    srv = json.loads((ROOT / "server.json").read_text())
    assert srv["$schema"].endswith("/2025-12-11/server.schema.json"), (
        f"server.json moved to {srv['$schema']}: re-read its limits and update this test"
    )
    assert 1 <= len(srv["description"]) <= 100, (
        f"description is {len(srv['description'])} chars; the registry allows 100"
    )
    assert 1 <= len(srv["title"]) <= 100, f"title is {len(srv['title'])} chars; max 100"
    assert 3 <= len(srv["name"]) <= 200
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", srv["name"]), srv["name"]
