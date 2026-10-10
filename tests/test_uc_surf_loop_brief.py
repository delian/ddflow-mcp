"""`brief` is one declared command (B-uc-surf-loop): its default is the api's, and the
three hand-written halves it replaced (parser, `cmd_brief`, a `TOOLS` entry) are gone."""

from __future__ import annotations

from ddflow.api.lifecycle import DEFAULT_CHECK_RECOVERY
from ddflow.surfaces.declared import lifecycle as declared
from ddflow.surfaces.mcp import TOOLS


def test_the_declared_default_is_the_apis():
    assert declared.DEFAULT_CHECK_RECOVERY is DEFAULT_CHECK_RECOVERY


def test_brief_is_served_from_its_declaration():
    assert TOOLS["ddflow_brief"] == declared.BY_TOOL["ddflow_brief"].tool_entry()
