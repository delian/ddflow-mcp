"""MTD-family-schema: a family is ONE tool with an ``action`` enum; its schema is the
deduplicated union of its members', ``as_agent`` is declared once, and a call routes to the
member with errors that name the action (D-mcp-tool-families)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.surfaces.registry import properties_schema
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
    assert schema.get("required") == (None if family in TOOLS else ["action"])
    action = schema["properties"]["action"]
    assert action["type"] == "string"
    assert action["enum"] == list(members)
    union = {n for t in members.values() for n in TOOLS[t]["properties"]}
    if "action" in union:
        union = (union - {"action"}) | {"op"}
    union |= {"as_agent"} if any(not TOOLS[t].get("identify") for t in members.values()) else set()
    assert set(schema["properties"]) == union | {"action"}
    for action_name in members:
        assert action_name in entry["description"]


@pytest.mark.parametrize("family", FAMILY_NAMES)
def test_as_agent_is_declared_once_exactly_when_a_member_takes_it(family):
    schema = FS.family_entry(family)["inputSchema"]
    takes = any(not TOOLS[t].get("identify") for t in F.FAMILIES[family].members.values())
    assert list(schema["properties"]).count("as_agent") == int(takes)


def test_a_property_two_members_type_differently_is_a_union_type():
    admin = FS.family_entry("ddflow_admin")["inputSchema"]["properties"]
    assert sorted(admin["apply"]["type"]) == ["boolean", "string"]
    assert sorted(admin["status"]["type"]) == ["boolean", "string"]
    assert FS.family_entry("ddflow_gate")["inputSchema"]["properties"]["id"]["type"] == "string"


def test_route_resolves_the_member_and_drops_the_action():
    r = FS.route(
        "ddflow_gate", {"action": "record", "id": "T1", "gate": "critic", "outcome": "passed"}
    )
    assert r.tool == "ddflow_gate_record"
    assert r.args == {"id": "T1", "gate": "critic", "outcome": "passed"}
    assert r.display == "ddflow_gate action=record"


def test_a_members_own_action_argument_is_op_in_its_family():
    assert not {t for t in TOOLS if "op" in TOOLS[t]["properties"]}
    owners = {t: F.placement(t) for t in TOOLS if "action" in TOOLS[t]["properties"]}
    assert len(owners) == 5
    for tool, (family, action) in owners.items():
        props = FS.family_entry(family)["inputSchema"]["properties"]
        assert props["action"]["enum"] and "op" in props
        assert action in props["op"]["description"]
        r = FS.route(family, {"action": action, "op": "status"})
        assert r.tool == tool
        assert r.args == {"action": "status"}


def test_op_is_unknown_for_an_action_that_has_no_action_argument_of_its_own():
    err = FS.route("ddflow_gate", {"action": "list", "op": "x"})
    assert isinstance(err, str)
    assert "ddflow_gate action=list" in err and "unknown argument(s): op" in err


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
    schema = properties_schema(FS.member_props(tool))
    return {"name": tool, "description": TOOLS[tool]["description"], "inputSchema": schema}


def test_missing_required_arguments_are_found_per_action():
    r = FS.Routed("ddflow_gate_record", {}, "ddflow_gate action=record")
    assert FS.missing_required(r) == ["id", "gate", "outcome"]
    err = FS.route("ddflow_gate", {"action": "record"})
    assert err == "ddflow_gate action=record: missing required argument(s): id, gate, outcome"
    r = FS.route("ddflow_gate", {"action": "record", "id": "T", "gate": "ci", "outcome": "passed"})
    assert FS.missing_required(r) == []


def test_an_argument_the_action_does_not_take_is_said_with_the_action_and_its_known_ones():
    err = FS.route("ddflow_gate", {"action": "list", "bogus": "x"})
    assert err.startswith("ddflow_gate action=list: unknown argument(s): bogus. Known: ")
    assert "as_agent" in err and "bogus" not in err.split("Known: ")[1]


def test_a_required_own_action_argument_is_listed_as_op_in_the_description(monkeypatch):
    props = dict(TOOLS["ddflow_queue"]["properties"])
    props["action"] = ("string", "add|run", True)
    monkeypatch.setitem(TOOLS["ddflow_queue"], "properties", props)
    desc = FS.family_entry("ddflow_view")["description"]
    assert re.search(r"\bqueue\([^)]*\bop\b", desc), desc
    assert not re.search(r"\bqueue\([^)]*\baction\b", desc), desc


def test_the_description_never_asks_for_an_argument_the_schema_lacks():
    for family in FAMILY_NAMES:
        entry = FS.family_entry(family)
        props = entry["inputSchema"]["properties"]
        listed = entry["description"].split("brackets: ", 1)[1]
        named = {
            n
            for group in re.findall(r"\(([^)]*)\)", listed)
            for n in map(str.strip, group.split(","))
            if n
        }
        assert named <= set(props), (family, named - set(props))
        for tool in F.FAMILIES[family].members.values():
            assert set(FS._requires(tool)) <= named


def test_an_explicit_null_action_is_a_missing_action_even_for_a_self_named_tool():
    err = FS.route("ddflow_doctor", {"action": None})
    assert isinstance(err, str) and "action" in err and "check" in err
