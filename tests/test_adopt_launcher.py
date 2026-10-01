"""`adopt --launch auto` registers `uvx ddflow-mcp` only for an install from an index.

Bug B8ff258154d: after `uv tool install git+https://github.com/delian/ddflow-mcp` (the
package not on PyPI), auto wrote `{"command": "uvx", "args": ["ddflow-mcp"]}` into every
agent's config -- a server that resolves the name against PyPI and cannot start. PEP 610
says how to tell: an install that did not come from an index carries `direct_url.json`
in its dist-info. These tests build that dist-info for real and let `importlib.metadata`
read it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import paths as PATHS
from ddflow.services import adopt as A

GIT = {
    "url": "https://github.com/example/ddflow-mcp",
    "vcs_info": {"vcs": "git", "commit_id": "0" * 40},
}


@pytest.fixture
def installed(tmp_path: Path, monkeypatch):
    """An installed (not source) ddflow in a tool environment: `bin/python`,
    `lib/.../site-packages/ddflow`, its dist-info, uvx and docker on PATH. Returns a
    function that writes `direct_url.json` (None = an index install) and optionally the
    `ddflow-mcp` script, then answers what auto would register."""
    env = tmp_path / "tool-env"
    site = env / "lib" / "python3" / "site-packages"
    (site / "ddflow").mkdir(parents=True)
    info = site / "ddflow_mcp-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 0.1.0\n")
    (env / "bin").mkdir()
    python = env / "bin" / "python"
    python.write_text("")

    monkeypatch.delenv(PATHS.LAUNCH_ROOT_ENV, raising=False)
    monkeypatch.setattr(PATHS, "package_parent", lambda: site)
    monkeypatch.setattr(A, "_running_from_source", lambda: False)
    monkeypatch.setattr(sys, "executable", str(python))
    on_path = {"uvx": "/usr/bin/uvx", "docker": "/usr/bin/docker"}
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: on_path.get(name))

    def entry(direct_url: dict | None, *, script: bool = True) -> dict:
        if direct_url is not None:
            (info / "direct_url.json").write_text(json.dumps(direct_url))
        if script:
            (env / "bin" / "ddflow-mcp").write_text("")
        return A._launch_entry("auto")

    entry.env, entry.site = env, site
    return entry


def test_a_git_install_registers_its_own_server_not_uvx(installed):
    entry = installed(GIT)
    assert entry["command"] != "uvx", entry
    assert entry == {"command": str(installed.env / "bin" / "ddflow-mcp"), "args": []}


def test_a_local_directory_install_registers_its_own_server(installed):
    entry = installed({"url": "file:///src/ddflow", "dir_info": {}})
    assert entry["command"] == str(installed.env / "bin" / "ddflow-mcp"), entry


def test_without_the_script_it_runs_the_interpreter_on_the_installed_package(installed):
    entry = installed(GIT, script=False)
    assert entry == {
        "command": str(installed.env / "bin" / "python"),
        "args": ["-m", A.MCP_MODULE],
        "env": {"PYTHONPATH": str(installed.site)},
    }


def test_a_non_index_install_wins_over_docker_when_uvx_is_missing(installed, monkeypatch):
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: "/usr/bin/docker" if name == "docker" else None
    )
    assert installed(GIT)["command"] == str(installed.env / "bin" / "ddflow-mcp")


def test_an_index_install_keeps_uvx(installed):
    assert installed(None) == {"command": "uvx", "args": ["ddflow-mcp"]}


def test_explicit_launch_choices_are_untouched(installed):
    installed(GIT)
    assert A._launch_entry("python")["args"] == ["-m", A.MCP_MODULE]
    assert A._launch_entry("docker")["command"] == "docker"


def test_an_editable_install_is_a_source_checkout_and_never_gets_uvx(installed, monkeypatch):
    """An editable install imports from the checkout, so `_running_from_source()` is
    True and the checkout's own entry is written -- before and after this fix. Guards
    the claim (rubber_duck on 825bb53) that the editable case falls through to uvx."""
    monkeypatch.setattr(A, "_running_from_source", lambda: True)
    entry = installed({"url": "file:///src/ddflow", "dir_info": {"editable": True}})
    assert entry["command"] == str(installed.env / "bin" / "python"), entry
    assert entry["args"] == ["-m", A.MCP_MODULE]


def test_a_target_dir_install_outside_site_packages_is_launched_from_that_dir(
    installed, monkeypatch, tmp_path
):
    """`pip install --target=/opt/vendor` puts the package where no path part is named
    site-packages, so it reads as a source tree: the entry is the interpreter with that
    directory on PYTHONPATH -- runnable -- and never uvx (critic on 1446b3b)."""
    vendor = tmp_path / "vendor"
    (vendor / "ddflow").mkdir(parents=True)
    monkeypatch.setattr(PATHS, "package_parent", lambda: vendor)
    monkeypatch.setattr(A, "_running_from_source", lambda: True)
    entry = installed(GIT)
    assert entry["command"] != "uvx", entry
    assert entry["env"] == {"PYTHONPATH": str(vendor)}
