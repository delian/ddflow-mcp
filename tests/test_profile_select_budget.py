"""`[mcp].tools = auto` picks the profile from the session's start budget too (LS-profile-select).

The profile is chosen ONCE, at `initialize`, never at runtime (`listChanged` stays false). A client
table entry says which profile a client can take; the session's EFFECTIVE start budget (the smaller
of the global and its agent type's, D-session-start-budget) only ever NARROWS it: a budget of at most
16K tokens is lean whatever the table says, at most 32K never wider than `families`; a larger or no
budget leaves the table's choice. An explicitly configured profile is never overridden.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import _KNOB_CHECKS
from ddflow.surfaces.mcp import Server
from ddflow.surfaces.tools import profiles as P


@pytest.fixture(autouse=True)
def _table(monkeypatch):
    monkeypatch.delenv("DDFLOW_MCP_TOOLS", raising=False)
    from ddflow.services import harnessreg as HR

    for h in HR.load():  # the harness running THIS suite must not pose as the client under test
        for var in h.env.session_id if h.env else ():
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(P.CLIENT_PROFILES, "bigclient", "hot+families")
    monkeypatch.setitem(P.CLIENT_PROFILES, "midclient", "families")


@pytest.mark.parametrize(
    ("budget", "client", "want"),
    [
        (0, "bigclient", "hot+families"),  # no budget: the table's word
        (40000, "bigclient", "hot+families"),  # a large budget: as the table says
        (32000, "bigclient", "families"),  # up to 32K: never wider than families
        (20000, "bigclient", "families"),
        (16000, "bigclient", "lean"),  # up to 16K: lean
        (8000, "bigclient", "lean"),
        (30000, "midclient", "families"),  # already no wider than families
        (12000, "midclient", "lean"),
        (8000, "", "lean"),  # an unknown client is lean already
        (50000, "", "lean"),  # and a large budget never widens it
    ],
)
def test_auto_is_narrowed_by_the_budget_and_never_widened(budget, client, want):
    assert P.settle("auto", client, budget=budget) == want


def test_the_default_limits_are_the_knob_defaults_and_the_table_below_follows_them():
    from ddflow.config import Config

    knobs = Config().mcp
    assert P.DEFAULT_LIMITS == (knobs.profile_lean_budget_tokens, knobs.profile_mid_budget_tokens)
    small, mid = P.DEFAULT_LIMITS
    assert P.settle("auto", "bigclient", budget=small) == "lean"
    assert P.settle("auto", "bigclient", budget=small + 1) == "families"
    assert P.settle("auto", "bigclient", budget=mid + 1) == "hot+families"


def test_an_explicit_profile_is_never_overridden_by_a_budget():
    assert P.settle("hot+families", "bigclient", budget=4000) == "hot+families"
    assert P.settle("families", "", budget=4000) == "families"
    assert P.settle("lean", "bigclient", budget=90000) == "lean"


def test_the_limits_are_the_callers_to_set():
    assert P.settle("auto", "bigclient", budget=20000, limits=(20000, 0)) == "lean"
    assert P.settle("auto", "bigclient", budget=20000, limits=(0, 0)) == "hot+families"


@pytest.mark.parametrize(
    "knob", ["mcp.profile_lean_budget_tokens", "mcp.profile_mid_budget_tokens"]
)
def test_the_limit_knobs_take_a_non_negative_whole_number(knob):
    assert _KNOB_CHECKS[knob](-1) and _KNOB_CHECKS[knob](True) and _KNOB_CHECKS[knob]("x")
    assert not _KNOB_CHECKS[knob](0) and not _KNOB_CHECKS[knob](16000)


def _tier_for(repo, client="bigclient"):
    srv = Server(repo)
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": client}},
        }
    )
    return srv.tier


def test_the_server_reads_the_effective_budget_at_initialize(repo):
    run_cli(repo, "init")
    assert _tier_for(repo) == "hot+families", "default budget 40000: the table's word"
    assert run_cli(repo, "config", "--set", "session.context_budget_tokens", "12000")[0] == 0
    assert _tier_for(repo) == "lean"
    assert run_cli(repo, "config", "--set", "session.context_budget_tokens", "30000")[0] == 0
    assert _tier_for(repo) == "families"


def test_an_agent_types_own_budget_narrows_only_that_client(repo):
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nclaude = 9000\n")
    P.CLIENT_PROFILES["claude-code"] = "hot+families"
    try:
        assert _tier_for(repo, "claude-code") == "lean", "the claude entry is 9000"
        assert _tier_for(repo, "bigclient") == "hot+families", "another client keeps the global"
    finally:
        P.CLIENT_PROFILES.pop("claude-code", None)


def test_a_configured_profile_in_the_project_ignores_the_budget(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "mcp.tools", "hot+families")[0] == 0
    assert run_cli(repo, "config", "--set", "session.context_budget_tokens", "8000")[0] == 0
    srv = Server(repo)
    assert srv.tier == "hot+families"
