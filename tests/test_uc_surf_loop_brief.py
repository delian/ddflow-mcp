"""`brief` is one declared command (B-uc-surf-loop): its default is the api's, and the
three hand-written halves it replaced (parser, `cmd_brief`, a `TOOLS` entry) are gone."""

from __future__ import annotations

from ddflow.api.lifecycle import DEFAULT_CHECK_RECOVERY
from ddflow.surfaces import tools
from ddflow.surfaces.commands import lifecycle as commands
from ddflow.surfaces.declared import lifecycle as declared


def test_the_declared_default_is_the_apis():
    assert declared.DEFAULT_CHECK_RECOVERY is DEFAULT_CHECK_RECOVERY


def test_brief_has_no_hand_written_half_left():
    assert "ddflow_brief" not in tools.HAND_WRITTEN
    assert not hasattr(commands, "cmd_brief")
    assert tools.TOOLS["ddflow_brief"] == declared.BY_TOOL["ddflow_brief"].tool_entry()
