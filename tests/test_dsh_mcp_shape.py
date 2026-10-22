"""The `cordis-patch-yaml` MCP shape: a ddflow-owned launch overlay (dsh), not a project config.

dsh reads MCP servers only from Cordis patch layers (docs/ddflow/research/dsh-probe.md), so
the writer owns ONE file of marker-tagged JSON rows. Rows, not a YAML library: JSON is YAML.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.services import harness, harnessreg
from ddflow.services import mcpconfig as MC

ENTRY = {"command": "ddflow", "args": ["mcp"], "env": {"DDFLOW_AGENT": "dsh"}, "cwd": "/repo"}


def _write(tmp: Path, name: str = "ddflow", entry: dict | None = None, **kw):
    return MC.register(
        tmp / ".ddflow/harness/dsh.cordis.yml",
        MC.SHAPE_CORDIS_PATCH,
        name,
        ENTRY if entry is None else entry,
        rel=".ddflow/harness/dsh.cordis.yml",
        **kw,
    )


def test_shape_is_in_the_descriptor_vocabulary():
    assert MC.SHAPE_CORDIS_PATCH in harnessreg.SHAPES


def test_row_is_the_dsh_mcp_client_insert_row(tmp_path):
    status, _ = _write(tmp_path)
    assert status == "written"
    text = (tmp_path / ".ddflow/harness/dsh.cordis.yml").read_text()
    assert "- insert:" in text and "# ddflow-row:ddflow" in text
    row = MC.cordis_rows(text)["ddflow"]
    assert row == {
        "id": "ddflow",
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {"serverName": "ddflow", "transport": "stdio", **ENTRY},
    }


def test_rewrite_is_idempotent_and_refreshes_a_stale_launch(tmp_path):
    assert _write(tmp_path)[0] == "written"
    assert _write(tmp_path)[0] == "unchanged"
    path = tmp_path / ".ddflow/harness/dsh.cordis.yml"
    before = path.read_text()
    status, msg = _write(tmp_path, entry={**ENTRY, "args": ["mcp", "--x"]})
    assert status == "written" and msg.startswith("refreshed")
    assert path.read_text().count("ddflow-row:ddflow") == 1 and path.read_text() != before


def test_operator_lines_and_other_rows_are_kept(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text('# mine\n- id: llm-pi-ai\n  config: {x: 1}\n- insert:\n    - {"id": "x"}\n')
    MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)
    text = path.read_text()
    assert "# mine" in text and "- id: llm-pi-ai" in text and '{"id": "x"}' in text
    assert "ddflow" in MC.cordis_rows(text)


def test_patch_entries_without_an_insert_list_are_refused_not_guessed(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text("- id: llm-pi-ai\n  config: {x: 1}\n")
    status, msg = MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)
    assert status == "refused" and "`- insert:`" in msg
    assert path.read_text() == "- id: llm-pi-ai\n  config: {x: 1}\n"


@pytest.mark.parametrize("name", ["", "a" * 33, "has space", "dot.name"])
def test_server_name_must_be_a_valid_dsh_server_name(tmp_path, name):
    status, msg = _write(tmp_path, name=name)
    assert status == "refused" and "serverName" in msg


def test_dry_run_writes_nothing(tmp_path):
    status, msg = _write(tmp_path, dry_run=True)
    assert status == "written" and msg.startswith("WOULD write")
    assert not (tmp_path / ".ddflow").exists()


def test_a_tagged_line_that_is_not_json_is_unknown_not_absent(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text("- insert:\n    - {oops  # ddflow-row:ddflow\n")
    assert MC.cordis_rows(path.read_text()) is None
    assert MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)[0] == "refused"


def test_harness_readers_understand_the_shape(tmp_path):
    _write(tmp_path)
    servers = harness.project_servers(
        tmp_path, mcp_rel=".ddflow/harness/dsh.cordis.yml", shape=MC.SHAPE_CORDIS_PATCH
    )
    assert servers["ddflow"]["command"] == "ddflow"
    h = type(
        "H",
        (),
        {
            "mcp": type(
                "M", (), {"path": ".ddflow/harness/dsh.cordis.yml", "shape": MC.SHAPE_CORDIS_PATCH}
            )()
        },
    )()
    assert harness.mcp_registered(tmp_path, h) is True
    (tmp_path / ".ddflow/harness/dsh.cordis.yml").write_text(
        "- insert:\n    - {bad  # ddflow-row:ddflow\n"
    )
    assert harness.mcp_registered(tmp_path, h) is None


def test_get_server_helpers_do_not_crash_on_the_yaml_shape():
    assert MC.get_servers({}, MC.SHAPE_CORDIS_PATCH) == {}
    assert MC.get_server({}, MC.SHAPE_CORDIS_PATCH, "ddflow") is None


def test_rows_are_valid_json_per_line(tmp_path):
    _write(tmp_path)
    for line in (tmp_path / ".ddflow/harness/dsh.cordis.yml").read_text().splitlines():
        if line.startswith("    - "):
            json.loads(line.split("- ", 1)[1].rsplit("  #", 1)[0])


def test_a_missing_overlay_is_no_servers_not_an_error(tmp_path):
    assert (
        harness.project_servers(
            tmp_path, mcp_rel=".ddflow/harness/dsh.cordis.yml", shape=MC.SHAPE_CORDIS_PATCH
        )
        == {}
    )


@pytest.mark.parametrize("bad", [None, "ddflow\n", 5])
def test_server_name_must_be_exactly_a_string_of_the_allowed_characters(tmp_path, bad):
    path = tmp_path / "o.yml"
    status, _ = MC._register_cordis(path, "o.yml", bad, ENTRY, False, "ddflow")
    assert status == "refused" and not path.exists()


def test_the_name_argument_wins_over_a_serverName_in_the_entry():
    row = MC.cordis_entry("ddflow", {"serverName": "evil", "command": "x"})
    assert row["id"] == "ddflow" and row["config"]["serverName"] == "ddflow"


def test_two_rows_for_one_name_are_unknown_not_resolved_by_position(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text(
        "- insert:\n"
        '    - {"id": "ddflow"}  # ddflow-row:ddflow\n'
        '    - {"id": "ddflow"}  # ddflow-row:ddflow\n'
    )
    assert MC.cordis_rows(path.read_text()) is None
    assert MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)[0] == "refused"


def test_two_insert_lists_are_refused_not_guessed(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text("- insert:\n    - {}\n- insert:\n    - {}\n")
    assert MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)[0] == "refused"


def test_a_row_without_an_insert_list_is_not_reported_as_refreshed(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text('    - {"id": "x"}  # ddflow-row:ddflow\n')
    assert MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)[0] == "refused"


def test_non_server_rows_are_not_listed_as_mcp_servers(tmp_path):
    _write(tmp_path)
    path = tmp_path / ".ddflow/harness/dsh.cordis.yml"
    path.write_text(path.read_text() + '    - {"id": "h", "name": "hooks"}  # ddflow-row:hooks\n')
    servers = harness.project_servers(
        tmp_path, mcp_rel=".ddflow/harness/dsh.cordis.yml", shape=MC.SHAPE_CORDIS_PATCH
    )
    assert list(servers) == ["ddflow"]


def test_a_hooks_row_in_the_one_insert_list_is_kept_beside_the_mcp_row(tmp_path):
    """One `- insert:` list holds every inserted plugin row (mcp client, hooks bridge)."""
    path = tmp_path / "o.yml"
    path.write_text('- insert:\n    - {"id": "h", "name": "hooks"}  # ddflow-row:hooks\n')
    assert MC.register(path, MC.SHAPE_CORDIS_PATCH, "ddflow", ENTRY)[0] == "written"
    text = path.read_text()
    assert set(MC.cordis_rows(text)) == {"hooks", "ddflow"}
    assert text.count("- insert:") == 1
    first, second = (text.index(f"ddflow-row:{n}") for n in ("hooks", "ddflow"))
    assert text.index("- insert:") < min(first, second)


def test_a_client_row_without_a_launch_config_is_not_a_server(tmp_path):
    path = tmp_path / "o.yml"
    path.write_text(
        "- insert:\n"
        '    - {"id": "ddflow", "name": "@deepseek-ai/dsh-mcp-client"}  # ddflow-row:ddflow\n'
    )
    assert MC.cordis_servers(MC.cordis_rows(path.read_text())) == {}
