"""MTD-profiles: the lean core (15 tools) is the default `tools/list`; the wide families (24),
hot+families (36) and the flat tiers are opt-in by `[mcp].tools` or the client table. Routing is
unchanged: whatever is advertised, every family and every old name stays callable
(Ddcde0f5b95, D-mcp-tool-families)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli, write_config

from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.tools import families as F
from ddflow.surfaces.tools import profiles as P

HOT = set(F.HOT_SINGLES)


def _rpc(srv, method, params=None, mid=1):
    return srv.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def _listed(repo, **init):
    srv = Server(repo)
    if init:
        _rpc(srv, "initialize", {"protocolVersion": "2025-03-26", **init})
    tools = _rpc(srv, "tools/list")["result"]["tools"]
    return srv, tools


def _names(tools):
    return [t["name"] for t in tools]


def _bytes(tools):
    return len(json.dumps({"tools": tools}, separators=(",", ":")).encode())


def test_the_profiles_and_their_counts():
    assert P.PROFILES == ("lean", "families", "hot+families")
    assert len(P.advertised_names("lean")) == 15
    assert len(P.advertised_names("families")) == 24
    assert len(P.advertised_names("hot+families")) == 36


def test_the_default_is_the_lean_core(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    srv, tools = _listed(repo)
    assert srv.tier == "lean"
    names = _names(tools)
    assert len(names) == 15
    assert HOT <= set(names)
    assert {"ddflow_gate", "ddflow_bug", "ddflow_session", "ddflow_task", "ddflow_view"} <= set(
        names
    )
    assert "ddflow_memory" in names
    assert _bytes(tools) < 33_000


@pytest.mark.parametrize(
    ("profile", "count"), [("lean", 15), ("families", 24), ("hot+families", 36)]
)
def test_each_profile_lists_its_tools_by_env(repo, monkeypatch, profile, count):
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", profile)
    _srv, tools = _listed(repo)
    assert len(tools) == count
    assert len(set(_names(tools))) == count


def test_the_knob_in_the_config_file_selects_a_profile(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    assert write_config(repo, [("mcp.tools", "families")])[0] in (None, "")
    _srv, tools = _listed(repo)
    assert len(tools) == 24


def test_the_flat_tiers_are_unchanged_and_opt_in(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "all")
    assert set(_names(_listed(repo)[1])) == set(TOOLS)
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "core")
    assert 25 < len(_listed(repo)[1]) < 60


def test_a_family_tool_has_the_action_enum_and_a_direct_tool_has_its_flat_schema(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "hot+families")
    tools = {t["name"]: t for t in _listed(repo)[1]}
    assert tools["ddflow_gate"]["inputSchema"]["properties"]["action"]["enum"]
    assert "action" not in tools["ddflow_gate_record"]["inputSchema"]["properties"]
    assert tools["ddflow_claim"]["inputSchema"]["required"] == ["id"]


def test_a_client_table_entry_picks_the_profile_for_auto_but_an_explicit_knob_wins(
    repo, monkeypatch
):
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    monkeypatch.setitem(P.CLIENT_PROFILES, "hosty", "families")
    _srv, tools = _listed(repo, clientInfo={"name": "Hosty", "version": "1"})
    assert len(tools) == 24
    _srv, tools = _listed(repo, clientInfo={"name": "other", "version": "1"})
    assert len(tools) == 15
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "lean")
    _srv, tools = _listed(repo, clientInfo={"name": "hosty", "version": "1"})
    assert len(tools) == 15


def test_the_shipped_client_table_names_only_known_profiles():
    assert set(P.CLIENT_PROFILES.values()) <= set(P.PROFILES)


def _call(srv, tool, **args):
    reply = _rpc(srv, "tools/call", {"name": tool, "arguments": args})
    result = reply["result"]
    return [c["text"] for c in result["content"]], bool(result.get("isError"))


def test_everything_stays_callable_on_the_lean_profile(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    srv = Server(repo)
    texts, err = _call(srv, "ddflow_rule", action="list")  # a family lean does not list
    assert not err, texts
    texts, err = _call(srv, "ddflow_rule_list")  # and its old name
    assert not err, texts


def test_an_old_name_is_noted_once_on_a_profile_that_hides_it_and_never_on_flat(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    srv = Server(repo)
    first, _ = _call(srv, "ddflow_rule_list")
    second, _ = _call(srv, "ddflow_rule_list")
    assert any("'ddflow_rule_list' is deprecated" in t for t in first), first
    assert not any("deprecated" in t for t in second), second
    texts, _ = _call(srv, "ddflow_claim", id="nope")  # a hot single is not an old name
    assert not any("deprecated since" in t for t in texts)
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "all")
    flat = Server(repo)
    texts, _ = _call(flat, "ddflow_rule_list")
    assert not any("deprecated" in t for t in texts)


def test_a_directly_listed_tool_on_hot_plus_families_is_not_noted(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "hot+families")
    srv = Server(repo)
    texts, _ = _call(srv, "ddflow_gate_status", id="nope")
    assert not any("deprecated since" in t for t in texts)
    texts, _ = _call(srv, "ddflow_rule_list")
    assert any("'ddflow_rule_list' is deprecated" in t for t in texts)


def test_the_tier_note_names_the_profile_and_how_to_widen_it():
    note = P.profile_note("lean")
    assert "`lean`" in note and "15" in note and "families" in note and "callable" in note
    assert "`families`" in P.profile_note("families") and "24" in P.profile_note("families")


def test_listchanged_stays_false(repo):
    srv = Server(repo)
    caps = _rpc(srv, "initialize", {"protocolVersion": "2025-03-26"})["result"]["capabilities"]
    assert caps["tools"].get("listChanged", False) is False
