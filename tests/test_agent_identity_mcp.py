"""An agent over MCP is never the operator, whatever its environment says
(fix-agent-identity-over-mcp-in-api; D-failed-review-gate).

`agent_marker` read only `--agent`, `DDFLOW_AGENT` and a harness variable, so a call that
reached the api with none of them -- an MCP connection in a process without the variables,
or `as_agent` empty -- passed as a person at a terminal and could grant the operator's
override. The MCP dispatch now marks the call itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import rpc_call
from test_complete_failed_review_gate import _failed_and_confirmed, _setup

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import identity as ID
from ddflow.surfaces.mcp import Server


@pytest.fixture
def bare_env(monkeypatch):
    for var in ("CLAUDECODE", "DDFLOW_AGENT", *ID.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _skip_text(repo, tool="ddflow_gate_skip", **extra):
    args = {"id": "T1", "gate": "critic", "reason": "operator accepts", **extra}
    result = rpc_call(Server(repo), tool, **args)
    return result.get("isError"), result["content"][0]["text"]


def test_an_mcp_call_is_an_agents_even_with_no_environment_marker(repo, tmp_path, bare_env):
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)
    assert ID.agent_marker("") == "", "the premise: nothing in the environment says agent"
    _err, text = _skip_text(repo)
    assert '"outcome":"refused"' in text and "operator" in text, text
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "failed"


def test_the_marker_is_scoped_to_the_call(bare_env):
    with ID.via_mcp():
        assert ID.agent_marker("") == "the MCP surface"
        assert ID.is_agent("", False)
    assert ID.agent_marker("") == "" and not ID.is_agent("", False)


def test_an_approval_cannot_be_granted_inside_an_mcp_call(repo, bare_env):
    """`chore approve` and every other grant ask `agent_marker` first."""
    from ddflow.services import approval as AP

    with ID.via_mcp(), pytest.raises(AP.ApprovalRefused, match="MCP"):
        AP.grant(EventLog(repo), subject="chore:x", digest="d")
