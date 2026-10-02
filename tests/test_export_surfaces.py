"""`ddflow export` over the CLI and `ddflow_export` over MCP (B-export-surfaces): exit codes,
CLI/MCP parity, the tool's tier and the write rules as a client sees them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces import mcp as M
from ddflow.surfaces.mcp import Server


@pytest.fixture
def proj(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "Billing")[0] == 0
    assert run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "Tax rules")[0] == 0
    return repo


def _call(repo: Path, args: dict) -> dict:
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_export", "arguments": args},
        }
    )["result"]
    # A refusal or failure is `_meta.exit` non-zero (the same code the CLI exits with).
    return {
        "error": bool(reply["_meta"]["exit"]),
        "exit": reply["_meta"]["exit"],
        "body": reply["content"][0]["text"],
    }


def test_list_prints_every_kind_not_selected(proj):
    code, out, _ = run_cli(proj, "export")
    assert code == 0
    for kind in ("roadmap", "bugs", "status", "worklog", "sessions", "decisions", "rules"):
        assert kind in out
    assert "not selected" in out and "selected: (none)" in out


def test_print_to_stdout_writes_no_file(proj):
    code, out, err = run_cli(proj, "export", "roadmap")
    assert code == 0 and out.startswith("<!-- ddflow:generated doc=roadmap")
    assert "Billing" in out and not (proj / "ROADMAP.md").exists()
    assert "redact" in err  # the honest note: redaction is not applied yet


def test_exit_codes_zero_one_two_three(proj):
    assert run_cli(proj, "export", "--all")[0] == 2  # nothing selected
    assert run_cli(proj, "export", "nosuch")[0] == 3  # unknown kind
    assert run_cli(proj, "export", "roadmap", "--check")[0] == 1  # missing = not fresh
    assert run_cli(proj, "export", "roadmap", "--update", "--yes")[0] == 0
    assert run_cli(proj, "export", "roadmap", "--check")[0] == 0
    (proj / "ROADMAP.md").write_text("# mine\n")
    code, _out, err = run_cli(proj, "export", "roadmap", "--update")
    assert code == 3 and "--force" in err and (proj / "ROADMAP.md").read_text() == "# mine\n"
    assert run_cli(proj, "export", "roadmap", "--update", "--force")[0] == 0


def test_diff_shows_the_change_and_writes_nothing(proj):
    run_cli(proj, "export", "roadmap", "--update")
    run_cli(proj, "task", "add", "P1.T2", "--phase", "P1", "--title", "Fresh work")
    before = (proj / "ROADMAP.md").read_text()
    code, out, _ = run_cli(proj, "export", "roadmap", "--diff")
    assert code == 0 and "Fresh work" in out and (proj / "ROADMAP.md").read_text() == before


def test_all_writes_exactly_the_selected_set(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export]\ndocuments = ["status"]\n')
    code = run_cli(proj, "export", "--all", "--update")[0]
    assert code == 0 and (proj / "STATUS.md").is_file() and not (proj / "ROADMAP.md").exists()
    assert run_cli(proj, "export", "--all", "--check")[0] == 0
    assert "fresh" in run_cli(proj, "export")[1]


def test_cli_and_mcp_return_the_same_list_and_document(proj):
    _c, cli_json, _ = run_cli(proj, "--json", "export")
    assert json.loads(_call(proj, {})["body"]) == json.loads(cli_json)
    _c, cli_doc, _ = run_cli(proj, "--json", "export", "status")
    mcp = json.loads(_call(proj, {"doc": "status"})["body"])
    assert mcp == json.loads(cli_doc)
    assert mcp["results"][0]["truncated"] is False


def test_mcp_never_writes_without_write_true_and_refuses_outside_paths(proj):
    assert not _call(proj, {"doc": "roadmap"})["error"]
    assert not (proj / "ROADMAP.md").exists()
    r = _call(proj, {"doc": "roadmap", "path": "ROADMAP.md"})
    assert r["error"] and not (proj / "ROADMAP.md").exists()
    r = _call(proj, {"doc": "roadmap", "write": True, "path": "../outside.md"})
    assert r["exit"] == 3 and not (proj.parent / "outside.md").exists()
    r = _call(proj, {"doc": "roadmap", "write": True, "path": "docs/ROADMAP.md"})
    assert not r["error"] and (proj / "docs" / "ROADMAP.md").is_file()


def test_mcp_truncation_is_explicit(proj):
    for i in range(20):
        run_cli(proj, "task", "add", f"P1.X{i}", "--phase", "P1", "--title", f"Task number {i}")
    r = json.loads(_call(proj, {"doc": "roadmap", "max_bytes": 400})["body"])["results"][0]
    assert r["truncated"] is True and r["bytes"] <= 400 and "[truncated:" in r["text"]


def test_the_tool_sits_in_exactly_one_tier_and_is_full_only():
    assert "ddflow_export" in M.FULL_ONLY_TOOLS
    assert (
        sum("ddflow_export" in s for s in (M.CORE_TOOLS, M.STANDARD_EXTRA_TOOLS, M.FULL_ONLY_TOOLS))
        == 1
    )
