"""Lane workers start on the core tool tier (LS-worker-mcp-config, P-lean-start).

A worker that loads every ddflow schema pays ~27K more than one on the `core` tier (measured
84.2K against 54.7K, docs/ddflow/research/lean-start.md lever 2), and the tier is chosen by an
environment variable that already exists. `adopt` writes a worker MCP config beside the project's
own, in the git-ignored local layer, and the doctor says when a host would send every schema.
Nothing is removed: every tool stays callable by name on any tier.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import MCP_TOOL_TIERS, Config
from ddflow.services import workermcp as WM
from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.tools.tiers import tier_tools

REL = ".ddflow/local/worker-mcp.json"


@pytest.fixture(autouse=True)
def _hermetic_tier_env(monkeypatch):
    """The tiers come from knobs; an inherited override must not decide a test."""
    for name in ("DDFLOW_MCP_TOOLS", "DDFLOW_MCP_WORKER_TOOLS", "ENABLE_TOOL_SEARCH"):
        monkeypatch.delenv(name, raising=False)


def test_the_knob_defaults_to_core_and_takes_only_known_tiers(repo):
    cfg = Config.load(repo)
    assert cfg.mcp.worker_tools == "core"
    assert "core" in MCP_TOOL_TIERS


def test_the_worker_entry_adds_the_tier_and_keeps_the_rest():
    entry = {"command": "uvx", "args": ["ddflow-mcp"], "env": {"PYTHONPATH": "/x"}}
    got = WM.worker_entry(entry, "core")
    assert got["env"] == {"PYTHONPATH": "/x", "DDFLOW_MCP_TOOLS": "core"}
    assert got["command"] == "uvx" and got["args"] == ["ddflow-mcp"]
    assert "DDFLOW_MCP_TOOLS" not in entry.get("env", {}), "the input must not be changed"
    assert WM.worker_entry({"command": "c"}, "standard")["env"] == {"DDFLOW_MCP_TOOLS": "standard"}


def test_a_docker_launch_forwards_the_tier_into_the_container():
    entry = {"command": "docker", "args": ["run", "-i", "--rm", "img"]}
    got = WM.worker_entry(entry, "core")
    assert got["args"] == ["run", "-e", "DDFLOW_MCP_TOOLS=core", "-i", "--rm", "img"], got
    assert entry["args"] == ["run", "-i", "--rm", "img"], "the input must not be changed"
    assert WM.worker_entry({"command": "uvx", "args": ["x"]}, "core")["args"] == ["x"]
    # not a `docker run`: left alone
    exec_entry = {"command": "docker", "args": ["exec", "worker", "run"]}
    assert WM.worker_entry(exec_entry, "core")["args"] == ["exec", "worker", "run"]
    # a tier already forwarded is replaced, not shadowed (the last -e would win)
    had = {"command": "docker", "args": ["run", "-e", "DDFLOW_MCP_TOOLS=all", "-e", "K=1", "img"]}
    assert WM.worker_entry(had, "core")["args"] == [
        "run",
        "-e",
        "DDFLOW_MCP_TOOLS=core",
        "-e",
        "K=1",
        "img",
    ]
    # arguments after the image belong to the container command, not to docker
    cmd = {"command": "docker", "args": ["run", "img"]}
    assert WM.worker_entry(cmd, "core")["args"] == ["run", "-e", "DDFLOW_MCP_TOOLS=core", "img"]
    again = WM.worker_entry(WM.worker_entry(had, "core"), "standard")
    assert again["args"].count("DDFLOW_MCP_TOOLS=standard") == 1
    assert not any("=core" in a for a in again["args"])


def test_adopt_for_claude_writes_the_worker_config_and_says_how_to_use_it(repo):
    code, out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    data = json.loads((repo / REL).read_text())
    assert data["mcpServers"]["ddflow"]["env"]["DDFLOW_MCP_TOOLS"] == "core"
    assert f"--mcp-config {REL}" in out and "--strict-mcp-config" in out, out
    code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0 and f"{REL} is current" in out, out


def test_the_knob_picks_the_tier_in_the_file(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "mcp.worker_tools", "standard")[0] == 0
    run_cli(repo, "adopt", "--agents", "claude")
    env = json.loads((repo / REL).read_text())["mcpServers"]["ddflow"]["env"]
    assert env["DDFLOW_MCP_TOOLS"] == "standard"


def test_the_file_is_in_the_git_ignored_local_layer(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    import subprocess

    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", REL], capture_output=True
    )
    assert ignored.returncode == 0, "the worker config holds this machine's launcher path"


def test_another_agent_gets_no_worker_config(repo):
    code, _out, err = run_cli(repo, "adopt", "--agents", "cursor")
    assert code == 0, err
    assert not (repo / REL).exists()


def test_a_server_started_with_that_env_lists_only_the_core_tier(repo, monkeypatch):
    run_cli(repo, "adopt", "--agents", "claude")
    env = json.loads((repo / REL).read_text())["mcpServers"]["ddflow"]["env"]
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", env["DDFLOW_MCP_TOOLS"])
    listed = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == set(tier_tools("core")) and len(names) < len(TOOLS)


def test_the_recipe_names_the_flags_and_the_deferral_switch():
    text = WM.recipe(REL)
    for must in ("--mcp-config", "--strict-mcp-config", "ENABLE_TOOL_SEARCH"):
        assert must in text, must


def test_the_doctor_note_says_when_a_host_would_send_every_schema():
    off = WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="all")
    assert off and "every ddflow schema" in off and "core" in off
    assert WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="core") == ""
    assert WM.deferral_note({"ENABLE_TOOL_SEARCH": "false"}, tier="standard") == ""
    assert WM.deferral_note({}, tier="all") == "", "default hosts defer; nothing to say"
    auto = WM.deferral_note({"ENABLE_TOOL_SEARCH": "auto:90"}, tier="all")
    assert auto, "auto:90 sets the threshold above ddflow's size: nothing deferred (measured)"


def test_doctor_judges_the_workers_tier_not_the_lanes_own(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    monkeypatch.delenv("DDFLOW_MCP_WORKER_TOOLS", raising=False)
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", "false")

    def notes() -> list[str]:
        return json.loads(run_cli(repo, "doctor", "--json")[1])["notes"]

    assert not any("every ddflow schema" in n for n in notes()), "workers default to core"
    run_cli(repo, "config", "--set", "mcp.worker_tools", "all")
    assert any("every ddflow schema" in n for n in notes())
    run_cli(repo, "config", "--set", "mcp.tools", "core")  # the lane's own tier does not matter
    assert any("every ddflow schema" in n for n in notes())
    monkeypatch.delenv("ENABLE_TOOL_SEARCH")
    assert not any("every ddflow schema" in n for n in notes())


def test_the_recipe_names_the_tier_the_file_carries(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_MCP_WORKER_TOOLS", raising=False)
    assert "only the `core` tier keeps that small" in WM.recipe(tier="all")
    assert "`standard` tier" in WM.recipe(tier="standard")
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "mcp.worker_tools", "standard")
    _code, out, _ = run_cli(repo, "adopt", "--agents", "claude")
    assert "`standard` tier" in out and "starts ddflow on the `core` tier" not in out


def test_the_unreleased_knob_fragment_loads_with_a_json_default():
    """`new` is JSON-encoded in a manifest fragment; a bare `core` made every `ddflow upgrade` fail."""
    from ddflow.services import upgrade_manifest as UM

    manifest = UM.load()  # raises ManifestError on a fragment it cannot read
    change = next(
        c for _v, _d, changes in manifest.releases for c in changes if c.key == "mcp.worker_tools"
    )
    assert change.new == "core"
