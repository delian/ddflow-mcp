"""Grok Build descriptor: hooks, provenance and the synthetic fixtures (DX-grok-hook-descriptor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.services import harnessfacts, hookio
from ddflow.services import harnessreg as HR

FIX = Path(__file__).parent / "fixtures" / "harness" / "grok"
H = HR.get("grok")


def test_descriptor_states_documented_hook_facts():
    assert H.hooks.style == "claude" and H.hooks.file == ".grok/hooks/ddflow.json"
    assert H.hooks.block == "exit2" and H.hooks.session_id == "sessionId"
    assert H.hooks.events["pre_tool"] == "PreToolUse" and H.hooks.events["stop"] == "Stop"
    assert H.mcp.path == ".grok/config.toml" and H.mcp.shape == "toml.mcp_servers"


def test_injection_is_left_out_because_it_is_not_verified():
    assert not H.hooks.inject  # third party only: never a guess


def test_every_fixture_is_labelled_synthetic_and_normalizes():
    files = sorted(FIX.glob("*.json"))
    assert {p.stem for p in files} == set(H.hooks.events)
    plan = hookio.plan_for("grok")
    for p in files:
        fx = json.loads(p.read_text())
        assert fx["_meta"]["recorded"] is False and fx["_meta"]["synthetic"] is True
        got = hookio.normalize(plan.normalizer, p.stem, "grok", json.dumps(fx["stdin"]))
        assert got.session_id == fx["expect"]["session_id"] == "s-1"
        assert got.prompt == fx["expect"].get("prompt")
        assert got.cwd == "/w"


def test_tool_event_carries_the_tool_name():
    fx = json.loads((FIX / "pre_tool.json").read_text())
    plan = hookio.plan_for("grok")
    got = hookio.normalize(plan.normalizer, "pre_tool", "grok", json.dumps(fx["stdin"]))
    assert got.tool == "Bash"


def test_facts_overlay_marks_documented_and_lists_unverified():
    rows = {r["fact"]: r for r in harnessfacts.rows(H)}
    assert rows["hooks.block"]["status"] == "documented"
    assert "hooks.inject" not in rows
    assert any("third party" in u for u in H.verified.unverified)


@pytest.mark.parametrize("key", ["headless.argv", "autonomy.profiles", "hooks.events"])
def test_overlay_cites_an_official_page(key):
    rows = {r["fact"]: r for r in harnessfacts.rows(H)}
    assert rows[key]["source"].startswith("https://docs.x.ai/build/")
