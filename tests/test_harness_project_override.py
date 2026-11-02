"""A project's harness overrides are read, not only the shipped descriptors.

Bug B-drv-descriptor-override-missing: adding-a-harness.md says every descriptor value
is overridable by a project, but ``harnessreg.load`` read only the shipped ``HARNESS_DIR``
and no caller passed another, so a project could not add or override an agent. ``load_in``
merges ``.ddflow/harnesses.d`` in, and ``harness show`` sees it.
"""

from __future__ import annotations

from ddflow.api.harness import harness_show
from ddflow.services import harnessreg

_GOOD = """
id = "demo"
rank = 99
family = "demo"
delta = "demo.md"

[mcp]
path = ".demo/mcp.json"
shape = "mcpServers"

[verified]
date = "2026-10-09"
sources = ["https://example.test/docs"]
"""


def test_load_in_reads_shipped_when_there_is_no_project(tmp_path):
    assert harnessreg.load_in(tmp_path) == harnessreg.load()


def test_a_project_override_replaces_the_shipped_descriptor(tmp_path):
    (tmp_path / ".ddflow" / "harnesses.d").mkdir(parents=True)
    override = _GOOD.replace('id = "demo"', 'id = "claude"').replace("rank = 99", "rank = 1")
    (tmp_path / ".ddflow" / "harnesses.d" / "claude.toml").write_text(override)
    by_id = {h.id: h for h in harnessreg.load_in(tmp_path)}
    assert by_id["claude"].family == "demo", "the override replaces the shipped descriptor"


def test_a_new_agent_in_the_project_dir_changes_harness_show(tmp_path):
    (tmp_path / ".ddflow" / "harnesses.d").mkdir(parents=True)
    (tmp_path / ".ddflow" / "harnesses.d" / "demo.toml").write_text(_GOOD)
    out = harness_show(tmp_path, "demo")
    assert out.exit == 0, out.reason
    assert out.data["id"] == "demo" and "demo (" in out.data["message"]
