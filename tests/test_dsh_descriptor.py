"""dsh as data: ddflow/harnesses/dsh.toml says what the probe showed, and no more."""

from __future__ import annotations

from pathlib import Path

from ddflow.api import harness as H
from ddflow.services import harnessreg as HR

ROOT = Path(__file__).resolve().parent.parent


def _dsh() -> HR.Harness:
    return HR.get("dsh")


def test_dsh_loads_with_the_launch_overlay_shape():
    h = _dsh()
    assert h.mcp.shape == "cordis-patch-yaml"
    assert h.mcp.path == ".ddflow/harness/dsh.cordis.yml"
    assert "--patch" in h.mcp.note


def test_family_is_the_mcp_client_key_not_a_model_family():
    # firstcall.py matches `family` as a substring of the MCP clientInfo.name; dsh sends
    # `dsh-mcp-client` (tests/fixtures/dsh_probe/mcp_handshake.ndjson).
    assert _dsh().family in "dsh-mcp-client"


def test_hooks_are_the_claude_bridge_with_only_the_events_it_has():
    hk = _dsh().hooks
    assert (hk.style, hk.block, hk.normalizer, hk.emitter) == (
        "claude",
        "exit2",
        "claude",
        "claude",
    )
    assert set(hk.events) == {"session_start", "prompt", "pre_tool", "post_tool", "stop"}
    assert "pre_compact" not in hk.events and "session_end" not in hk.events
    assert set(hk.inject) == {"session_start", "prompt"}


def test_absent_events_are_not_credited_with_a_plugin_ddflow_does_not_ship():
    h = _dsh()
    assert h.plugin is None  # DSH-bundle is deferred
    assert H.event_level(h, "pre_compact") != H.PLUGIN
    assert H.event_level(h, "session_end") != H.PLUGIN
    assert H.event_level(h, "pre_tool") == H.NATIVE


def test_what_could_not_be_checked_is_listed_not_guessed():
    h = _dsh()
    assert h.commands is None and h.subagents is None and h.worktrees is None
    assert any("subagents" in u for u in h.verified.unverified)
    assert h.env.session_id == ("DSH_SESSION_ID",)
    assert h.skills.dir == ".agents/skills"


def test_instructions_read_both_files():
    i = _dsh().instructions
    assert (i.agents_md, i.claude_md) == ("yes", "yes") and not i.native_path


def test_the_delta_note_exists_and_names_the_launch_line():
    text = (ROOT / "ddflow/templates/drivers/deltas" / _dsh().delta).read_text()
    assert "--patch .ddflow/harness/dsh.cordis.yml" in text and "model" in text


def test_dsh_is_in_the_readme_table_of_supported_agents():
    readme = (ROOT / "README.md").read_text()
    assert "| dsh (DeepSeek Harness) | `dsh` | `.ddflow/harness/dsh.cordis.yml` |" in readme


def test_adopt_writes_the_overlay_not_a_project_mcp_file(repo):
    from conftest import run_cli

    from ddflow.services import mcpconfig as MC

    assert run_cli(repo, "adopt", "--agents", "dsh")[0] == 0
    overlay = repo / ".ddflow/harness/dsh.cordis.yml"
    rows = MC.cordis_rows(overlay.read_text())
    assert MC.cordis_servers(rows)["ddflow"]["serverName"] == "ddflow"
    assert not (repo / ".mcp.json").exists()
    assert (repo / "docs/ddflow/drivers/deltas/dsh.md").is_file()
