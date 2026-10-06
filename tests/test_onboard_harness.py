"""Onboarding stage 1: approving the project's MCP servers, pinned to the failure it
prevents -- a project `.mcp.json` server Claude Code never starts fails silently: nothing
errors, the workflow simply behaves as if the queue were absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import harness as H
from ddflow.services.adopt import Refused
from ddflow.services.claudehooks import SettingsError

PIN = "/opt/ddflow-checkout"
ENTRY = {
    "command": "/opt/ddflow-checkout/.venv/bin/python",
    "args": ["-m", "ddflow.surfaces.mcp"],
    "env": {"PYTHONPATH": PIN},
}
MCP = {"mcpServers": {"ddflow": ENTRY}}


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _settings(repo: Path) -> dict:
    return json.loads((repo / H.SETTINGS_REL).read_text())


# --- enabledMcpjsonServers -------------------------------------------------------------


def test_the_registered_servers_are_approved(repo):
    _write(repo / H.MCP_REL, MCP)
    actions = H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == ["ddflow"]
    assert not [a for a in actions if isinstance(a, Refused)]
    assert any("approved ddflow" in a for a in actions)


def test_every_registered_server_is_approved_not_only_ddflow(repo):
    _write(repo / H.MCP_REL, {"mcpServers": {"ddflow": ENTRY, "other": {"command": "x"}}})
    H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == ["ddflow", "other"]


def test_the_operators_settings_and_approvals_survive_and_install_is_idempotent(repo):
    _write(
        repo / H.SETTINGS_REL,
        {"permissions": {"defaultMode": "plan"}, H.ENABLED_KEY: ["other"], H.DISABLED_KEY: ["old"]},
    )
    _write(repo / H.MCP_REL, MCP)
    H.enable_project_servers(repo)
    first = _settings(repo)
    assert first[H.ENABLED_KEY] == ["other", "ddflow"]
    assert first["permissions"] == {"defaultMode": "plan"}
    assert first[H.DISABLED_KEY] == ["old"]
    actions = H.enable_project_servers(repo)
    assert _settings(repo) == first
    assert any("already approved" in a for a in actions)


def test_a_disabled_server_is_reported_not_silently_re_enabled(repo):
    """`disabledMcpjsonServers` rejects the server in every permission mode, so clearing
    it here would reverse a decision this function has no mandate to touch."""
    _write(repo / H.SETTINGS_REL, {H.DISABLED_KEY: ["ddflow"]})
    _write(repo / H.MCP_REL, MCP)
    actions = H.enable_project_servers(repo)
    denied = [a for a in actions if isinstance(a, Refused)]
    assert len(denied) == 1 and "disabledMcpjsonServers" in denied[0]
    assert H.ENABLED_KEY not in _settings(repo)
    assert _settings(repo)[H.DISABLED_KEY] == ["ddflow"]


def test_an_unreadable_settings_file_is_left_alone(repo):
    path = repo / H.SETTINGS_REL
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)
    assert path.read_text() == "{ not json"


def test_a_non_list_approval_key_refuses_rather_than_overwrites(repo):
    _write(repo / H.SETTINGS_REL, {H.ENABLED_KEY: "ddflow"})
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)


@pytest.mark.parametrize("bad", [{}, "", 0, False, None])
def test_a_falsy_non_list_key_refuses_rather_than_overwrites(repo, bad):
    """`get(k) or []` turned `{}`/`""`/`false` into a list and rewrote it (rubber_duck)."""
    _write(repo / H.SETTINGS_REL, {H.ENABLED_KEY: bad})
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == bad


def test_no_registered_servers_is_a_no_op(repo):
    assert "nothing to approve" in H.enable_project_servers(repo)[0]
    assert not (repo / H.SETTINGS_REL).exists(), "a settings file was created for no server"


def test_an_unreadable_mcp_file_refuses_rather_than_approves_nothing(repo):
    (repo / H.MCP_REL).write_text("{ nope")
    with pytest.raises(H.HarnessError):
        H.project_servers(repo)


def test_an_mcp_file_that_is_not_an_object_refuses(repo):
    (repo / H.MCP_REL).write_text("[]")
    with pytest.raises(H.HarnessError):
        H.project_servers(repo)
