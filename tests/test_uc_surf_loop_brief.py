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
    assert "ddflow_brief" in declared.BY_TOOL


def test_declaring_the_loop_commands_needs_no_template_engine():
    """`scripts/bump.sh` imports the MCP engine under an interpreter without jinja2: the
    renderers of `declared/*_cli.py` may not reach the modules that import it."""
    import subprocess
    import sys

    code = "import sys; sys.modules['jinja2'] = None; import ddflow.surfaces.mcp"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
