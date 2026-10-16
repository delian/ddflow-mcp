"""`ddflow brief --for-agent NAME ITEM`: the standard launch brief of a subagent (DX-agent-brief).

The standard text lives in one template; the item adds its own part; config can switch sections
off, cap the length and add project text; a project template replaces the shipped one."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config_sections.session import AGENT_BRIEF_SECTIONS
from ddflow.services import agent_brief as AB
from ddflow.surfaces.mcp import Server


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T1",
        "--title",
        "Widen the widget",
        "--body",
        "Make the widget wider without touching the gadget.",
        "--globs",
        "src/widget.py,tests/test_widget.py",
    )
    return repo


def brief(repo, *extra, name="sub-1", item="T1"):
    return run_cli(repo, "brief", "--for-agent", name, item, *extra)


def test_the_brief_carries_the_item_the_agent_flag_the_pin_recipe_and_the_lease_rule(proj):
    code, out, err = brief(proj)
    assert code == 0, err
    assert "`T1`" in out and "Widen the widget" in out
    assert "--agent sub-1" in out
    assert "ddflow pins regenerate" in out
    assert "ddflow heartbeat T1" in out and "ddflow claim T1" in out
    assert "src/widget.py, tests/test_widget.py" in out
    assert "git merge main" in out
    for rule in ("git stash", "pkill", "ddflow tests --item T1", "ddflow review show T1"):
        assert rule in out, rule


def test_a_note_and_the_project_addition_ride_along(proj):
    run_cli(proj, "config", "session.agent_brief_extra", "Run the suite with make check.")
    out = brief(proj, "--note", "start with the parser")[1]
    assert "start with the parser" in out
    assert "## This project" in out and "make check" in out


def test_a_governing_decision_is_quoted(proj):
    run_cli(
        proj,
        "decision",
        "add",
        "--id",
        "D-wide",
        "--title",
        "Widgets are wide",
        "--decision",
        "Every widget is at least 80 columns.",
        "--globs",
        "src/widget.py",
    )
    out = brief(proj)[1]
    assert 'id="D-wide"' in out and "Every widget is at least 80 columns." in out


def test_json_has_the_sections_and_the_brief(proj):
    code, out, _ = run_cli(proj, "--json", "brief", "--for-agent", "sub-1", "T1")
    data = json.loads(out)
    assert code == 0 and data["for_agent"] == "sub-1" and data["item"] == "T1"
    assert set(data["sections"]) <= set(AGENT_BRIEF_SECTIONS)
    assert {"identity", "item", "start", "tests", "reviews", "forbidden", "report"} <= set(
        data["sections"]
    )
    assert data["sections"]["identity"] in data["brief"]


def test_a_section_can_be_switched_off_by_config(proj):
    assert run_cli(proj, "config", "session.agent_brief_off", "filing,forbidden")[0] == 0
    sections = json.loads(run_cli(proj, "--json", "brief", "--for-agent", "a", "T1")[1])["sections"]
    assert "filing" not in sections and "forbidden" not in sections and "start" in sections


def test_an_unknown_section_name_is_refused_by_config(proj):
    assert run_cli(proj, "config", "session.agent_brief_off", "nonsense")[0] != 0


def test_the_ceiling_cuts_decisions_first_and_never_the_rules(proj):
    run_cli(
        proj,
        "decision",
        "add",
        "--id",
        "D-wide",
        "--title",
        "Widgets are wide",
        "--decision",
        "Every widget is at least 80 columns.",
        "--globs",
        "src/widget.py",
    )
    whole = json.loads(run_cli(proj, "--json", "brief", "--for-agent", "a", "T1")[1])
    assert "decisions" in whole["sections"] and whole["over"] is False
    run_cli(proj, "config", "session.agent_brief_max_chars", str(len(whole["brief"]) - 20))
    cut = json.loads(run_cli(proj, "--json", "brief", "--for-agent", "a", "T1")[1])
    assert "pins regenerate" in cut["brief"] and len(cut["brief"]) < len(whole["brief"])
    run_cli(proj, "config", "session.agent_brief_max_chars", "10")
    tiny = json.loads(run_cli(proj, "--json", "brief", "--for-agent", "a", "T1")[1])
    assert "pins regenerate" in tiny["brief"] and tiny["over"] is True
    assert "decisions" not in tiny["sections"] and "lessons" not in tiny["sections"]


def test_the_ceiling_leaves_a_short_brief_whole(proj):
    run_cli(proj, "config", "session.agent_brief_max_chars", "0")
    assert "pins regenerate" in brief(proj)[1]


def test_a_project_template_replaces_the_shipped_text(proj):
    d = proj / ".ddflow" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent_brief.md").write_text(
        "<!-- section: identity -->\nCompany agent {{ agent }} on {{ item }}.\n"
        "<!-- section: start -->\nGo.\n"
    )
    out = brief(proj)[1]
    assert out.startswith("Company agent sub-1 on T1.") and "pins regenerate" not in out


def test_an_unknown_item_and_a_bad_agent_name_fail(proj):
    assert brief(proj, item="NOPE")[0] == 1
    assert brief(proj, name="a b")[0] == 1
    assert brief(proj, name="x; rm -rf /")[0] == 1


def test_brief_without_for_agent_is_still_the_session_pack(proj):
    out = run_cli(proj, "brief")[1]
    assert "## Start" not in out and "ddflow subagent" not in out


def test_mcp_serves_the_launch_brief(proj):
    reply = Server(proj).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_brief", "arguments": {"item": "T1", "for_agent": "m1"}},
        }
    )
    body = json.dumps(reply["result"])
    assert "--agent m1" in body and "pins regenerate" in body


def test_the_template_splits_into_the_declared_sections():
    text = (Path(AB.__file__).parents[1] / "templates" / "prompts" / "agent_brief.md").read_text()
    assert tuple(n for n, _ in AB.split(text)) == AGENT_BRIEF_SECTIONS
