"""A per-call `as_agent` is the caller's real identity, and its shell takes it too.

A subagent names itself per call with `as_agent` (D-mcp-identity-per-call). Its shell is
a different process from its MCP connection, and derives a name from the working tree
unless the name is recorded for the harness. A shelled-out `ddflow review` then attributed
the review to a derived name instead of to the reviewer (B-ddflow-review-wrong-agent-name),
so reviewer independence could pass against the author reviewing their own work. A legacy
`ddflow_identify` records the name for the shell (Bfad021e8d9); a per-call name is recorded
once, on the call that carries it, and the CLI (the shell's `ddflow review` above all) reads
it back instead of deriving.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import rpc_call as _call

from ddflow.infra import harness_identity as H
from ddflow.surfaces.mcp import Server

needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")


@needs_proc
def test_a_per_call_name_is_recorded_for_the_agents_own_shell(repo):
    """A call naming itself `as_agent` leaves the name for the harness, so the SAME agent's
    shell — the process that runs `ddflow review` — reports that name under `ddflow
    identify` instead of deriving the tree name."""
    run_cli(repo, "init")
    srv = Server(repo)
    _call(srv, "ddflow_brief", as_agent="sub-1")
    env = {k: v for k, v in os.environ.items() if k != "DDFLOW_AGENT"}
    try:
        cli = subprocess.run(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "identify", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr
        said = json.loads(cli.stdout)
        assert said["agent"] == "sub-1", "the shell's CLI named the wrong identity: " + cli.stdout
        assert said["source"] == "ddflow_identify", (
            "the shell did not take the harness record: " + cli.stdout
        )
    finally:
        H.declare(repo, "")
