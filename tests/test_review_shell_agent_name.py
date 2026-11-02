"""A per-call `as_agent` is the caller's real identity, and its shell takes it too.

A subagent names itself per call with `as_agent` (D-mcp-identity-per-call). Its shell is
a different process from its MCP connection, and derives a name from the working tree
unless the name is recorded for the harness. A shelled-out `ddflow review` then attributed
the review to a derived name instead of to the reviewer (B-ddflow-review-wrong-agent-name),
so reviewer independence could pass against the author reviewing their own work. A legacy
`ddflow_identify` records the name for the shell (Bfad021e8d9); a per-call name is recorded
once, on the call that carries it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import rpc_call as _call

from ddflow.api.harness import HR
from ddflow.infra import harness_identity as H
from ddflow.surfaces.mcp import Server

needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")


@needs_proc
def test_a_per_call_name_is_recorded_for_the_agents_own_shell(repo):
    """A call that names itself `as_agent` leaves the name for the harness, so the same
    agent's shell inherits it instead of deriving the tree name (which is what a named
    reviewer's shelled `ddflow review` records against)."""
    run_cli(repo, "init")
    srv = Server(repo)
    _call(srv, "ddflow_brief", as_agent="sub-1")
    scope = HR.session_fingerprint(os.environ)
    try:
        assert H.declared(repo, scope=scope) == "sub-1"
    finally:
        H.declare(repo, "")
