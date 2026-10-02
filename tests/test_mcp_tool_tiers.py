"""`[mcp].tools = core | standard | all`: which tools `tools/list` advertises.

A start-time context-cost knob (D-lean-and-trusted (4)). It never removes a tool: the
registry `TOOLS` stays whole, a tool outside the tier is still callable by name, and
`listChanged` stays false. The parity ratchets in `tests/test_mcp_parity.py` read `TOOLS`,
not `tools/list`, so they are exempt from the tier by construction (see EXEMPTION there).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.config import MCP_TOOL_TIERS, Config
from ddflow.surfaces import mcp as M
from ddflow.surfaces.mcp import TOOLS, Server, _schema

CORE_BUDGET = 40_000


def _size(tools: list[dict]) -> int:
    return len(json.dumps({"tools": tools}, separators=(",", ":")))


def _list(repo: Path) -> list[dict]:
    return Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"][
        "tools"
    ]


def _with_tier(repo: Path, tier: str, monkeypatch) -> None:
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", tier)


def test_every_registered_tool_is_in_exactly_one_tier_set():
    sets = (M.CORE_TOOLS, M.STANDARD_EXTRA_TOOLS, M.FULL_ONLY_TOOLS)
    for i, a in enumerate(sets):
        for b in sets[i + 1 :]:
            assert not (a & b), sorted(a & b)
    assert set().union(*sets) == set(TOOLS), (
        "unplaced: "
        f"{sorted(set(TOOLS) - set().union(*sets))}; stale: {sorted(set().union(*sets) - set(TOOLS))}"
    )


def test_config_and_surface_agree_on_the_tier_names():
    assert tuple(MCP_TOOL_TIERS) == tuple(M.TIERS)
    assert Config().mcp.tools == M.DEFAULT_TIER == "all"


def test_default_is_all_and_byte_identical_to_the_unfiltered_list(repo):
    expected = [
        {"name": n, "description": s["description"], "inputSchema": _schema(s)}
        for n, s in sorted(TOOLS.items())
    ]
    assert _list(repo) == expected
    assert json.dumps(_list(repo)) == json.dumps(expected)


def test_sizes_core_under_budget_standard_between(repo, monkeypatch):
    sizes = {}
    for tier in MCP_TOOL_TIERS:
        _with_tier(repo, tier, monkeypatch)
        sizes[tier] = _size(_list(repo))
    assert sizes["core"] <= CORE_BUDGET, sizes
    assert sizes["core"] < sizes["standard"] < sizes["all"], sizes


def test_tier_lists_exactly_its_tools(repo, monkeypatch):
    _with_tier(repo, "core", monkeypatch)
    assert {t["name"] for t in _list(repo)} == set(M.CORE_TOOLS)
    _with_tier(repo, "standard", monkeypatch)
    assert {t["name"] for t in _list(repo)} == set(M.CORE_TOOLS | M.STANDARD_EXTRA_TOOLS)


def test_config_file_sets_the_tier_and_env_overrides_it(repo, monkeypatch):
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text('[mcp]\ntools = "core"\n')
    assert Server(repo).tier == "core"
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "standard")
    assert Server(repo).tier == "standard"


def test_unknown_tier_is_refused_on_write_but_lists_everything_at_start(repo, monkeypatch):
    with pytest.raises(ValueError, match="core, standard, all"):
        Config.check({"mcp": {"tools": "tiny"}})
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text('[mcp]\ntools = "tiny"\n')
    assert Server(repo).tier == "all"
    assert len(_list(repo)) == len(TOOLS)


def test_hidden_tool_is_still_callable_and_named_by_help(repo, monkeypatch):
    _with_tier(repo, "core", monkeypatch)
    srv = Server(repo)
    hidden = "ddflow_loops"
    assert hidden not in {t["name"] for t in _list(repo)}
    called = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": hidden, "arguments": {}},
        }
    )["result"]
    assert "unknown tool" not in json.dumps(called)
    helped = srv.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ddflow_help"}}
    )["result"]
    text = " ".join(c["text"] for c in helped["content"])
    assert hidden in text and "`standard` or `all`" in text and "DDFLOW_MCP_TOOLS" in text


def test_help_says_nothing_about_tiers_at_all(repo):
    helped = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ddflow_help"}}
    )["result"]
    assert "tool tier" not in " ".join(c["text"] for c in helped["content"])


def test_list_changed_stays_false_and_handshake_names_the_tier(repo, monkeypatch):
    _with_tier(repo, "core", monkeypatch)
    init = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})[
        "result"
    ]
    assert init["capabilities"]["tools"] == {"listChanged": False}
    assert "`core` tool tier" in init["instructions"]
    assert "DDFLOW_MCP_TOOLS" in init["instructions"]
    monkeypatch.setenv("DDFLOW_MCP_TOOLS", "all")
    init = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})[
        "result"
    ]
    assert "tool tier" not in init["instructions"]


def test_the_budget_ratchet_applies_to_the_full_list(repo):
    from test_mcp_tool_budget import TOOLS_LIST_BUDGET

    assert _size(_list(repo)) <= TOOLS_LIST_BUDGET
