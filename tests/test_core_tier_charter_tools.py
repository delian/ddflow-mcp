"""Every tool the charter tells an agent to call is in the core tier (fix of B7752a21d68).

The MCP charter names `ddflow_rule_show` and `ddflow_rule_search` as the way to read any rule in full
(D-rules-charter), and the lean role definitions name `ddflow_release` and `ddflow_review_triage`. On
a core-tier connection (the default for lane workers) a tool outside the tier is not listed, so a
client that only calls listed tools could not reach them: the contract promised what the tier hid.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_charter_contract import _base

from ddflow.surfaces.tools.tiers import tier_tools


def test_every_tool_the_base_charter_names_is_in_the_core_tier(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    named = set(re.findall(r"`(ddflow_[a-z_]+)", _base(repo)))
    assert {"ddflow_brief", "ddflow_rule_show", "ddflow_rule_search"} <= named, named
    missing = sorted(named - set(tier_tools("core")))
    assert not missing, f"the charter names tools the core tier does not list: {missing}"


def test_the_tools_a_role_needs_to_finish_its_work_are_in_the_core_tier():
    for name in (
        "ddflow_release",
        "ddflow_review_triage",
        "ddflow_rule_show",
        "ddflow_rule_search",
    ):
        assert name in tier_tools("core"), name
