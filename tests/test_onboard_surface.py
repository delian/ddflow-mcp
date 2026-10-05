"""The onboard surfaces: one dispatcher behind CLI and MCP, every stage reachable.

The CLI-verb/MCP-name parity, the tier placement and the wire shape are ratcheted by
tests/test_mcp_parity.py, test_mcp_tool_tiers.py, test_api_layer.py and test_help.py;
this module pins the dispatcher's own contract -- one stage list, no guessing, and the
report (not a write) as the default answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import onboard as A
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS


def _cli_stages() -> tuple[str, ...]:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices
    onboard = choices["onboard"]
    stage = next(a for a in onboard._actions if a.dest == "stage")
    return tuple(stage.choices)


def test_the_cli_and_the_dispatcher_offer_the_same_stages():
    assert _cli_stages() == A.STAGES


def test_the_tool_lambda_reaches_the_dispatcher(repo):
    assert "ddflow_onboard" in TOOLS
    out = TOOLS["ddflow_onboard"]["api"](repo, {"stage": "preflight"}, "")
    assert out.data["text"], out


def test_an_unknown_stage_is_refused_with_the_list(repo):
    out = A.onboard(repo, stage="nope")
    assert out.exit == 3 and "status" in out.reason


def test_status_reports_the_checks_but_does_not_run_the_suite(repo):
    out = A.onboard(repo, stage="status")
    assert out.exit in (0, 1), out.reason
    names = [c["name"] for c in out.data["checks"]]
    assert "mcp handshake" in names and "suite green" not in names


def test_preflight_still_reports_nothing_on_a_clean_repo(repo):
    out = A.onboard(repo, stage="preflight")
    assert out.exit == 2 and "nothing left behind" in out.data["text"]


def test_legacy_apply_without_imports_writes_nothing(repo):
    """Apply on a repo with nothing imported is a no-op, not an empty freeze."""
    out = A.onboard(repo, stage="legacy", apply=True)
    assert out.exit == 2
    assert not (repo / ".ddflow" / "frozen.toml").exists()


def test_legacy_accepting_an_unknown_file_is_refused(repo):
    out = A.onboard(repo, stage="legacy", apply=True, accept=["nope.md"])
    assert out.exit == 3 and "nothing approved to freeze" in out.reason
    assert not (repo / ".ddflow" / "frozen.toml").exists()


def test_memory_accepting_an_unknown_name_is_refused(repo):
    """A typo used to exit 0 having recorded nothing (reviews on 2130b17)."""
    out = A.onboard(repo, stage="memory", apply=True, accept=["M-harness-nope"])
    assert out.exit == 3
    assert out.data["refused"] and out.data["refused"][0]["name"] == "M-harness-nope"
