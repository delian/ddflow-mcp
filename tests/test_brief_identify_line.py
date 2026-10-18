"""The brief gives the exact identify call, and `ddflow_identify` takes `name` as an alias
(DX-brief-identify-argument; D-compat)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_the_brief_names_the_identify_argument(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "x", "--globs", "a.py")
    out = run_cli(repo, "brief", "--for-agent", "w1", "T1")[1]
    assert (
        '`ddflow_identify` only on a connection of your own, with the argument `agent="w1"`, not `name`'
        in out
    )
    assert "`as_agent` on each call" in out
    assert "DDFLOW_AGENT=w1" in out


def test_identify_accepts_name_as_an_alias(repo):
    from ddflow.surfaces import mcp

    spec = mcp.TOOLS["ddflow_identify"]
    assert "name" in spec["arg_aliases"]
    name, spec2, args, used, clash = mcp._resolve_call("ddflow_identify", {"name": "w1"})
    assert args == {"agent": "w1"} and not clash and used
    assert mcp.Server._check_call(name, spec2, args, clash) == ""
