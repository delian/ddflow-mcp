"""MTD-family-schema: a family is ONE tool with an ``action`` enum; its schema is the
deduplicated union of its members', ``as_agent`` is declared once, and a call routes to the
member with errors that name the action (D-mcp-tool-families)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server
from ddflow.surfaces.tools import TOOLS
from ddflow.surfaces.tools import families as F
from ddflow.surfaces.tools import family_schema as FS

FAMILY_NAMES = [f.name for f in F.FAMILIES.values() if not f.single]


def test_one_listing_per_family_and_none_for_singles():
    names = [e["name"] for e in FS.family_listing()]
    assert names == FAMILY_NAMES
    assert len(names) == 15


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_schema_has_the_action_enum_and_the_union_of_the_members_arguments(family):
    entry = FS.family_entry(family)
    schema = entry["inputSchema"]
    members = F.FAMILIES[family].members
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action"]
    action = schema["properties"]["action"]
    assert action["type"] == "string"
    assert action["enum"] == list(members)
    union = {n for t in members.values() for n in TOOLS[t]["properties"]}
    union |= {"as_agent"} if any(not TOOLS[t].get("identify") for t in members.values()) else set()
    assert set(schema["properties"]) == union | {"action"}
    for action_name in members:
        assert action_name in entry["description"]


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_as_agent_is_declared_once_and_no_member_requires_anything_in_the_schema(family):
    schema = FS.family_entry(family)["inputSchema"]
    assert list(schema["properties"]).count("as_agent") == 1
    assert schema["required"] == ["action"]


def test_a_property_two_members_type_differently_is_a_union_type():
    admin = FS.family_entry("ddflow_admin")["inputSchema"]["properties"]
    assert sorted(admin["apply"]["type"]) == ["boolean", "string"]
    assert sorted(admin["status"]["type"]) == ["boolean", "string"]
    assert FS.family_entry("ddflow_gate")["inputSchema"]["properties"]["id"]["type"] == "string"


def test_route_resolves_the_member_and_drops_the_action():
    r = FS.route("ddflow_gate", {"action": "record", "id": "T1", "gate": "critic"})
    assert r.tool == "ddflow_gate_record"
    assert r.args == {"id": "T1", "gate": "critic"}
    assert r.display == "ddflow_gate action=record"


def test_route_of_a_name_that_is_no_family_or_a_hot_single_is_none():
    assert FS.route("ddflow_brief", {"action": "x"}) is None
    assert FS.route("ddflow_nope", {}) is None
    assert FS.route("ddflow_gate_record", {"id": "x"}) is None


def test_an_unknown_action_names_the_actions():
    err = FS.route("ddflow_gate", {"action": "frobnicate"})
    assert isinstance(err, str)
    assert "frobnicate" in err
    for a in F.FAMILIES["ddflow_gate"].members:
        assert a in err


def test_a_missing_action_names_the_actions():
    err = FS.route("ddflow_bug", {"summary": "s"})
    assert isinstance(err, str)
    assert "action" in err
    assert "found" in err


def test_self_named_family_tools_without_an_action_are_the_legacy_tool():
    assert FS.route("ddflow_doctor", {}) is None
    assert FS.route("ddflow_workflow", {}) is None
    r = FS.route("ddflow_doctor", {"action": "check"})
    assert r.tool == "ddflow_doctor"


def test_the_whole_families_surface_is_much_smaller_than_the_flat_one():
    flat = sum(len(json.dumps(_flat(t), separators=(",", ":"))) for t in TOOLS)
    fams = sum(len(json.dumps(e, separators=(",", ":"))) for e in FS.family_listing())
    singles = sum(len(json.dumps(_flat(t), separators=(",", ":"))) for t in F.HOT_SINGLES)
    assert fams + singles < 0.6 * flat
    assert fams + singles < 55_000  # measured 49,629 (the research estimated 53,312)


def _flat(tool):
    from ddflow.surfaces.mcp import _schema

    s = TOOLS[tool]
    return {"name": tool, "description": s["description"], "inputSchema": _schema(s)}


def _call(repo, tool, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    result = reply["result"]
    return result["content"][0]["text"], bool(result.get("isError"))


@pytest.fixture
def inited(repo):
    run_cli(repo, "init")
    return repo


def test_a_family_call_runs_the_member_and_gives_the_flat_answer(inited):
    fam, fam_err = _call(inited, "ddflow_gate", action="list")
    flat, flat_err = _call(inited, "ddflow_gate_list")
    assert (fam, fam_err) == (flat, flat_err)
    assert not fam_err


def test_a_missing_required_argument_names_the_action(inited):
    text, err = _call(inited, "ddflow_gate", action="record")
    assert err
    assert "ddflow_gate action=record" in text
    assert "missing required argument(s)" in text


def test_an_argument_of_another_action_is_unknown_for_this_one(inited):
    text, err = _call(inited, "ddflow_gate", action="list", gate="critic", bogus="x")
    assert err
    assert "ddflow_gate action=list" in text
    assert "bogus" in text


def test_an_unknown_or_missing_action_is_an_error_naming_the_actions(inited):
    text, err = _call(inited, "ddflow_gate", action="frobnicate")
    assert err and "frobnicate" in text and "record" in text
    text, err = _call(inited, "ddflow_gate")
    assert err and "action" in text and "record" in text


def test_the_legacy_names_and_the_self_named_tools_still_answer_as_before(inited):
    text, err = _call(inited, "ddflow_doctor")
    assert not err
    text2, err2 = _call(inited, "ddflow_doctor", action="check")
    assert not err2
    assert text2 == text
    assert _call(inited, "ddflow_gate_list")[1] is False
