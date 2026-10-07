"""One MCP registration writer for `adopt` and `companions add` (B-uni-hooks.2-mcpconfig).

`adopt` decided a TOML agent config already registered ddflow by finding the header as
TEXT (Ba4cc85bdbc): a stale launch was never refreshed, and a commented-out header
counted as a registration while codex had no ddflow server at all. It now goes through
`mcpconfig.register`, the writer `companions add` already used.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from ddflow.services import adopt as A
from ddflow.services import companions as CO
from ddflow.services import mcpconfig as MC

CODEX = ".codex/config.toml"


def _codex(repo: Path, text: str) -> Path:
    p = repo / CODEX
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ddflow_in(p: Path) -> dict:
    return tomllib.loads(p.read_text("utf-8"))["mcp_servers"]["ddflow"]


# --- Ba4cc85bdbc: adopt and a TOML agent config --------------------------------------


def test_adopt_refreshes_a_stale_ddflow_entry_in_a_toml_config(tmp_path: Path) -> None:
    p = _codex(
        tmp_path,
        '[mcp_servers.other]\ncommand = "x"\nargs = []\n\n'
        '[mcp_servers.ddflow]\ncommand = "/gone/python"\nargs = ["-m", "old"]\n',
    )
    msg = A._register_mcp(tmp_path, "codex", launch="python")
    want = A._launch_entry("python")
    assert msg == f"refreshed ddflow in {CODEX}"
    assert _ddflow_in(p) == want
    assert tomllib.loads(p.read_text("utf-8"))["mcp_servers"]["other"] == {
        "command": "x",
        "args": [],
    }


def test_adopt_does_not_take_a_commented_header_for_a_registration(tmp_path: Path) -> None:
    p = _codex(tmp_path, "# [mcp_servers.ddflow] was here\n")
    msg = A._register_mcp(tmp_path, "codex", launch="python")
    assert msg == f"registered ddflow in {CODEX}"
    assert _ddflow_in(p) == A._launch_entry("python")
    assert p.read_text("utf-8").startswith("# [mcp_servers.ddflow] was here\n")


def test_adopt_leaves_a_current_toml_entry_alone(tmp_path: Path) -> None:
    A._register_mcp(tmp_path, "codex", launch="python")
    before = (tmp_path / CODEX).read_text("utf-8")
    assert (
        A._register_mcp(tmp_path, "codex", launch="python") == f"{CODEX} already registers ddflow"
    )
    assert (tmp_path / CODEX).read_text("utf-8") == before


def test_adopt_refuses_a_toml_config_it_cannot_parse(tmp_path: Path) -> None:
    p = _codex(tmp_path, "[mcp_servers\nbroken")
    msg = A._register_mcp(tmp_path, "codex", launch="python")
    assert isinstance(msg, A.Refused)
    assert msg == f"SKIPPED {CODEX}: it is not valid TOML; add the server by hand"
    assert p.read_text("utf-8") == "[mcp_servers\nbroken"


# --- JSON: what adopt kept ------------------------------------------------------------


def test_adopt_json_messages_and_refusals_are_unchanged(tmp_path: Path) -> None:
    assert A._register_mcp(tmp_path, "claude", launch="python") == "registered ddflow in .mcp.json"
    data = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
    assert data["mcpServers"]["ddflow"] == A._launch_entry("python")
    (tmp_path / ".mcp.json").write_text("{not json", "utf-8")
    msg = A._register_mcp(tmp_path, "claude", launch="python")
    assert isinstance(msg, A.Refused)
    assert msg == "SKIPPED .mcp.json: it is not valid JSON; add the server by hand"
    (tmp_path / ".mcp.json").write_text('{"mcpServers": ["x"]}', "utf-8")
    msg = A._register_mcp(tmp_path, "claude", launch="python")
    assert isinstance(msg, A.Refused) and msg.startswith("SKIPPED .mcp.json: not a JSON object")


def test_adopt_does_not_rewrite_a_json_config_that_already_holds_its_entry(tmp_path: Path) -> None:
    """A re-adopt used to re-serialise the operator's file every time."""
    entry = A.server_entry_for(A.SHAPE_MCP_SERVERS, A._launch_entry("python"))
    text = json.dumps({"mcpServers": {"ddflow": entry}, "other": 1}, indent=4)
    (tmp_path / ".mcp.json").write_text(text, "utf-8")
    msg = A._register_mcp(tmp_path, "claude", launch="python")
    assert msg == ".mcp.json already registers ddflow with the same launch command"
    assert (tmp_path / ".mcp.json").read_text("utf-8") == text


# --- the shared writer ----------------------------------------------------------------


@pytest.mark.parametrize("shape", [MC.SHAPE_MCP_SERVERS, MC.SHAPE_TOML])
def test_a_dry_run_touches_nothing_and_names_what_it_would_write(tmp_path: Path, shape) -> None:
    p = tmp_path / ("c.toml" if shape == MC.SHAPE_TOML else "c.json")
    status, msg = MC.register(p, shape, "srv", {"command": "x", "args": ["-y"]}, dry_run=True)
    assert status == "written" and msg.startswith(f"WOULD add to {p}:")
    assert not p.exists()
    status, msg = MC.register(p, shape, "srv", {"command": "x", "args": ["-y"]})
    assert (status, msg) == ("written", f"registered srv in {p}")


def test_the_old_import_paths_still_resolve() -> None:
    for name in ("SHAPE_TOML", "place_server", "get_server", "get_servers", "server_entry_for"):
        assert getattr(A, name) is getattr(MC, name)
    assert A.UnplaceableConfig is MC.UnplaceableConfig
    for name in ("_toml_without", "_load_toml", "_declared", "_serves", "_launch_of"):
        assert getattr(CO, name) is getattr(MC, name)
