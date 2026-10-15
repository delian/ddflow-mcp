"""The actor resolver (SE-actors): roles are a config table, one reader, fail closed."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.services import actors as A


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


def test_terminal_is_operator_and_holds_everything():
    a = A.resolve_actor(Config(), "me")
    assert (a.role, a.source) == ("operator", "terminal")
    assert a.can("waive") and a.can("anything-at-all")


def test_agent_marker_or_mcp_is_default_role():
    cfg = Config()
    for kw in ({"requested_agent": "x"}, {"via_mcp": True}):
        a = A.resolve_actor(cfg, "x", **kw)
        assert (a.role, a.source) == ("agent", "default")
        assert a.can("claim") and not a.can("approve")
    assert A.require(a, "waive").startswith("x acts as role 'agent'")
    assert A.require(a, "claim") == ""


def test_harness_marker_is_not_a_person(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert A.resolve_actor(Config(), "x").role == "agent"


def test_named_actor_needs_a_proved_id():
    cfg = Config()
    cfg.agent.actors = {"openclaw": "bot"}
    spoof = A.resolve_actor(cfg, "openclaw", requested_agent="openclaw")
    assert spoof.role == "agent"  # saying the name is not proof
    real = A.resolve_actor(cfg, "openclaw", requested_agent="openclaw", authenticated=True)
    assert (real.role, real.source) == ("bot", "named")
    assert real.can("start_lane") and real.can("approve_marked") and not real.can("approve")


def test_credential_role_wins_and_unknown_role_fails_closed():
    cfg = Config()
    assert A.resolve_actor(cfg, "t", via_mcp=True, credential_role="observer").capabilities == {
        "read"
    }
    ghost = A.resolve_actor(cfg, "t", via_mcp=True, credential_role="ghost")
    assert not ghost.can("read") and A.require(ghost, "read")


def test_capability_is_a_name_in_the_table():
    cfg = Config()
    cfg.agent.roles = {**cfg.agent.roles, "agent": ["audit"]}
    a = A.resolve_actor(cfg, "x", via_mcp=True)
    assert a.can("audit") and not a.can("claim")


def test_family_and_router_come_from_the_review_maps():
    cfg = Config()
    a = A.resolve_actor(cfg, "x", via_mcp=True, model="claude-opus")
    assert a.family == "anthropic" and a.families == ()
    assert A.resolve_actor(cfg, "x", via_mcp=True, model="mystery").family == ""
    cfg.agent.routers = {"hydrafusion": ["openai", "google"]}
    r = A.resolve_actor(cfg, "x", via_mcp=True, model="hydrafusion-1")
    assert r.family == "" and r.families == ("google", "openai")
