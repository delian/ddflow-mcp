"""SU-digest: the interface digest and the soft/hard classifier (D-soft-upgrade-rule).

The digest is a sha256 over the canonical JSON of what a client can see and negotiate:
tool names, schemas, descriptions and annotations; prompt names and arguments; the
instructions template id; capabilities; protocol versions; the shim and worker protocol.
`classify` applies the five-rung rule: a shim or protocol change is HARD and overrides.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.surfaces import ifacedigest as D

ROOT = Path(__file__).resolve().parents[1]
GOVERNS = ("ddflow/surfaces/ifacedigest.py",)

SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "mode": {"type": "string", "enum": ["a", "b"]}},
    "required": ["id"],
}


def tool(name="ddflow_x", description="does x", schema=None, annotations=None):
    t = {
        "name": name,
        "description": description,
        "inputSchema": copy.deepcopy(schema if schema is not None else SCHEMA),
    }
    if annotations is not None:
        t["annotations"] = annotations
    return t


def snap(tools=None, prompts=None, **kw):
    kw.setdefault("instructions_id", "tpl-1")
    kw.setdefault("capabilities", {"tools": {"listChanged": False}})
    kw.setdefault("protocols", ["2025-11-25", "2025-06-18"])
    return D.snapshot(
        tools=tools if tools is not None else [tool()],
        prompts=prompts if prompts is not None else [],
        **kw,
    )


# ---- the digest ------------------------------------------------------------------


def test_digest_is_a_sha256_hex_string():
    d = D.digest(snap())
    assert len(d) == 64 and int(d, 16) >= 0


def test_digest_is_independent_of_tool_prompt_and_key_order():
    a = snap(
        tools=[tool("b"), tool("a")],
        prompts=[{"name": "q", "arguments": []}, {"name": "p", "arguments": []}],
    )
    b = snap(
        tools=[tool("a"), tool("b")],
        prompts=[{"name": "p", "arguments": []}, {"name": "q", "arguments": []}],
    )
    assert D.digest(a) == D.digest(b)
    reordered = {
        "inputSchema": {"required": ["id"], "properties": SCHEMA["properties"], "type": "object"},
        "description": "does x",
        "name": "ddflow_x",
    }
    assert D.digest(snap(tools=[reordered])) == D.digest(snap())


def test_digest_is_pinned_so_the_canonical_form_cannot_drift_silently():
    golden = snap(
        tools=[tool("ddflow_a", "d", {"type": "object", "properties": {}})],
        prompts=[{"name": "p", "arguments": [{"name": "x", "required": False}]}],
    )
    assert D.digest(golden) == "5487138ff242dbc14042f88b8237126e2549473d29256170631c987b0f56a4a0"


@pytest.mark.parametrize(
    "field,mutate",
    [
        ("tool description", lambda: snap(tools=[tool(description="does y")])),
        ("tool schema", lambda: snap(tools=[tool(schema={"type": "object", "properties": {}})])),
        ("tool annotation", lambda: snap(tools=[tool(annotations={"readOnlyHint": True})])),
        ("tool added", lambda: snap(tools=[tool(), tool("ddflow_y")])),
        ("prompt added", lambda: snap(prompts=[{"name": "p", "arguments": []}])),
        ("instructions id", lambda: snap(instructions_id="tpl-2")),
        ("capabilities", lambda: snap(capabilities={"tools": {"listChanged": True}})),
        ("protocols", lambda: snap(protocols=["2025-11-25"])),
        ("shim protocol", lambda: snap(shim_proto=2)),
        ("worker protocol", lambda: snap(worker_proto=2)),
    ],
)
def test_every_visible_field_changes_the_digest(field, mutate):
    assert D.digest(mutate()) != D.digest(snap()), field


def test_prompt_argument_changes_the_digest():
    a = snap(prompts=[{"name": "p", "arguments": [{"name": "x", "required": False}]}])
    b = snap(prompts=[{"name": "p", "arguments": [{"name": "x", "required": True}]}])
    assert D.digest(a) != D.digest(b)


def test_prompt_description_is_not_part_of_the_digest():
    a = snap(prompts=[{"name": "p", "description": "one", "arguments": []}])
    b = snap(prompts=[{"name": "p", "description": "two", "arguments": []}])
    assert D.digest(a) == D.digest(b)


def test_the_canonical_form_is_compact_sorted_json():
    text = D.canonical(snap())
    assert json.loads(text) == json.loads(D.canonical(json.loads(text)))
    assert ", " not in text and ": " not in text


# ---- the live surface -------------------------------------------------------------


def _live_digest_script(repo: Path) -> str:
    return (
        "from pathlib import Path\n"
        "from ddflow.surfaces import ifacedigest as D\n"
        "from ddflow.surfaces.mcp import Server\n"
        f"s = Server(Path({str(repo)!r}))\n"
        "print(D.digest(D.live_snapshot(s)))\n"
    )


def _init_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_live_digest_is_identical_across_processes(tmp_path):
    repo = _init_repo(tmp_path / "r")
    outs = {
        subprocess.run(
            [sys.executable, "-c", _live_digest_script(repo)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(outs) == 1 and len(next(iter(outs))) == 64


def test_live_snapshot_leaves_out_the_release_version(tmp_path, monkeypatch):
    from ddflow.surfaces import mcp_protocol
    from ddflow.surfaces.mcp import Server

    repo = _init_repo(tmp_path / "r")
    monkeypatch.setitem(mcp_protocol.SERVER_INFO, "version", "1.1.1")
    before = D.digest(D.live_snapshot(Server(repo)))
    monkeypatch.setitem(mcp_protocol.SERVER_INFO, "version", "99.99.99")
    assert D.digest(D.live_snapshot(Server(repo))) == before
    assert "99.99.99" not in D.canonical(D.live_snapshot(Server(repo)))


def test_each_tier_has_its_own_digest(tmp_path):
    from ddflow.surfaces.mcp import Server

    repo = _init_repo(tmp_path / "r")
    srv = Server(repo)
    srv.tier = "core"
    core = D.digest(D.live_snapshot(srv))
    srv.tier = "all"
    assert D.digest(D.live_snapshot(srv)) != core


def test_facade_snapshot_lists_exactly_the_two_facade_tools(tmp_path, monkeypatch):
    from ddflow.surfaces import mcp
    from ddflow.surfaces.mcp import Server

    # The facade tools (MTD-search-call) are not registered yet: register stand-ins so the
    # filter is tested against a registry that has them.
    spec = next(iter(mcp.TOOLS.values()))
    for name in D.FACADE_TOOLS:
        monkeypatch.setitem(mcp.TOOLS, name, spec)
    s = D.live_snapshot(Server(_init_repo(tmp_path / "r")), facade=True)
    assert {t["name"] for t in s["tools"]} == set(D.FACADE_TOOLS)


def test_facade_snapshot_is_empty_until_the_facade_tools_exist(tmp_path):
    from ddflow.surfaces.mcp import Server

    s = D.live_snapshot(Server(_init_repo(tmp_path / "r")), facade=True)
    assert s["tools"] == []


# ---- schema compatibility ------------------------------------------------------------


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def test_a_new_optional_argument_is_compatible():
    new = _obj({**SCHEMA["properties"], "extra": {"type": "integer"}}, ["id"])
    assert D.schema_problems(SCHEMA, new) == []


def test_a_new_required_argument_is_incompatible():
    new = _obj(SCHEMA["properties"], ["id", "mode"])
    assert any("mode" in p and "required" in p for p in D.schema_problems(SCHEMA, new))


def test_a_removed_argument_is_incompatible():
    new = _obj({"id": {"type": "string"}}, ["id"])
    assert any("mode" in p and "removed" in p for p in D.schema_problems(SCHEMA, new))


def test_a_changed_type_is_incompatible():
    new = _obj({"id": {"type": "integer"}, "mode": SCHEMA["properties"]["mode"]}, ["id"])
    assert any("id" in p and "type" in p for p in D.schema_problems(SCHEMA, new))


def test_a_narrowed_enum_is_incompatible_and_a_widened_one_is_not():
    narrow = _obj({"id": {"type": "string"}, "mode": {"type": "string", "enum": ["a"]}}, ["id"])
    wide = _obj(
        {"id": {"type": "string"}, "mode": {"type": "string", "enum": ["a", "b", "c"]}}, ["id"]
    )
    assert any("enum" in p for p in D.schema_problems(SCHEMA, narrow))
    assert D.schema_problems(SCHEMA, wide) == []


def test_a_nested_property_is_checked():
    old = _obj({"o": _obj({"k": {"type": "string"}}, [])})
    new = _obj({"o": _obj({"k": {"type": "string"}}, ["k"])})
    assert any(p.startswith("o:") and "k" in p for p in D.schema_problems(old, new))


# ---- the five-rung rule ----------------------------------------------------------------


def test_rung1_an_unchanged_digest_is_soft_and_announces_nothing():
    v = D.classify(snap(), snap(), "ignores")
    assert (v.kind, v.rung, v.announce) == ("soft", 1, False)


def test_rung2_a_client_that_honours_list_changed_is_soft_and_announced():
    v = D.classify(snap(), snap(tools=[tool(description="new")]), "honours")
    assert (v.kind, v.rung, v.announce) == ("soft", 2, True)


def test_rung2_covers_a_removed_tool_for_an_honouring_client():
    v = D.classify(snap(tools=[tool(), tool("ddflow_y")]), snap(), "honours")
    assert v.kind == "soft" and v.rung == 2


def test_rung3_an_additive_change_is_soft_for_a_client_that_ignores_list_changed():
    new = snap(tools=[tool(), tool("ddflow_new")])
    v = D.classify(snap(), new, "ignores")
    assert (v.kind, v.rung, v.announce) == ("soft", 3, False)
    assert v.pending == ("tool ddflow_new",)


def test_rung3_a_compatible_schema_and_a_description_change_are_additive():
    new = snap(
        tools=[
            tool(
                description="better",
                schema=_obj({**SCHEMA["properties"], "x": {"type": "string"}}, ["id"]),
            )
        ]
    )
    assert D.classify(snap(), new, "unknown").rung == 3


def test_an_unknown_client_is_treated_as_one_that_ignores():
    new = snap(tools=[tool(schema=_obj({"id": {"type": "string"}}, ["id"]))])
    v = D.classify(snap(), new, "unknown", schema_grace=False)
    assert v.kind == "hard"


def test_rung4_an_incompatible_schema_is_soft_with_schema_grace():
    new = snap(tools=[tool(schema=_obj(SCHEMA["properties"], ["id", "mode"]))])
    v = D.classify(snap(), new, "ignores", schema_grace=True)
    assert (v.kind, v.rung) == ("soft", 4)
    assert any("mode" in r for r in v.reasons)


def test_rung5_an_incompatible_schema_without_grace_is_hard_for_an_ignoring_client():
    new = snap(tools=[tool(schema=_obj(SCHEMA["properties"], ["id", "mode"]))])
    v = D.classify(snap(), new, "ignores", schema_grace=False)
    assert (v.kind, v.rung) == ("hard", 5)


def test_rung5_a_removed_tool_is_hard_for_an_ignoring_client_even_with_grace():
    v = D.classify(snap(tools=[tool(), tool("ddflow_y")]), snap(), "ignores", schema_grace=True)
    assert (v.kind, v.rung) == ("hard", 5)
    assert any("ddflow_y" in r and "removed" in r for r in v.reasons)


def test_rung5_a_new_required_prompt_argument_is_hard_for_an_ignoring_client():
    old = snap(prompts=[{"name": "p", "arguments": [{"name": "x", "required": False}]}])
    new = snap(prompts=[{"name": "p", "arguments": [{"name": "x", "required": True}]}])
    assert D.classify(old, new, "ignores").kind == "hard"


def test_a_removed_prompt_is_hard_for_an_ignoring_client():
    old = snap(prompts=[{"name": "p", "arguments": []}])
    assert D.classify(old, snap(), "ignores").kind == "hard"


@pytest.mark.parametrize("client", ["honours", "ignores", "unknown"])
@pytest.mark.parametrize("facade", [False, True])
def test_a_shim_protocol_change_is_hard_and_overrides_everything(client, facade):
    v = D.classify(snap(), snap(shim_proto=2), client, facade=facade)
    assert (v.kind, v.rung) == ("hard", 5)
    assert any("shim" in r for r in v.reasons)


def test_a_worker_protocol_change_is_hard_even_when_the_digest_is_otherwise_unchanged():
    a, b = snap(), snap(worker_proto=2)
    v = D.classify(a, b, "honours")
    assert v.kind == "hard" and any("worker" in r for r in v.reasons)


def test_a_changed_capability_set_is_hard_because_it_is_negotiated_at_initialize():
    v = D.classify(snap(), snap(capabilities={"tools": {"listChanged": True}}), "honours")
    assert v.kind == "hard" and any("capabilit" in r for r in v.reasons)


def test_a_dropped_protocol_version_is_hard_and_an_added_one_is_not():
    assert D.classify(snap(), snap(protocols=["2025-11-25"]), "honours").kind == "hard"
    more = snap(protocols=["2026-07-28", "2025-11-25", "2025-06-18"])
    assert D.classify(snap(), more, "ignores").kind == "soft"


def test_a_changed_instructions_template_is_soft_and_pending_for_the_next_session():
    v = D.classify(snap(), snap(instructions_id="tpl-2"), "ignores")
    assert v.kind == "soft" and v.rung == 3
    assert any("instructions" in p for p in v.pending)


def test_facade_mode_hides_changes_to_the_tools_behind_it():
    facade = [tool("ddflow_search"), tool("ddflow_call")]
    old = snap(tools=[*facade, tool("ddflow_inner")])
    new = snap(tools=[*facade, tool("ddflow_inner", schema=_obj({}, []))])
    v = D.classify(old, new, "ignores", facade=True, schema_grace=False)
    assert (v.kind, v.rung) == ("soft", 1)
    assert any("facade" in r for r in v.reasons)


def test_facade_mode_still_hard_when_the_facade_tools_themselves_change_incompatibly():
    old = snap(tools=[tool("ddflow_search"), tool("ddflow_call")])
    new = snap(
        tools=[
            tool("ddflow_search"),
            tool("ddflow_call", schema=_obj(SCHEMA["properties"], ["id", "mode"])),
        ]
    )
    v = D.classify(old, new, "ignores", facade=True, schema_grace=False)
    assert v.kind == "hard"


def test_the_verdict_is_deterministic_and_lists_reasons_in_a_stable_order():
    old = snap(tools=[tool("a"), tool("b"), tool("c")])
    new = snap(tools=[tool("c", description="z"), tool("d")])
    first = D.classify(old, new, "ignores")
    assert first == D.classify(old, new, "ignores")
    assert first.reasons[1:] == ("tool a removed", "tool b removed")


def test_worst_picks_the_hardest_verdict_across_surfaces():
    soft = D.classify(snap(), snap(), "ignores")
    hard = D.classify(snap(), snap(shim_proto=2), "ignores")
    assert D.worst([soft, hard, soft]) is hard
    assert D.worst([soft]) is soft


def test_classify_surfaces_compares_each_named_surface_and_takes_the_worst():
    old = {"all": snap(), "core": snap()}
    new = {"all": snap(), "core": snap(shim_proto=2)}
    v = D.classify_surfaces(old, new, "honours")
    assert v.kind == "hard" and any("core" in r for r in v.reasons)


def test_integer_to_number_is_a_broadening_and_untyped_to_typed_is_a_restriction():
    assert (
        D.schema_problems(_obj({"n": {"type": "integer"}}), _obj({"n": {"type": "number"}})) == []
    )
    assert D.schema_problems(_obj({"n": {"type": "number"}}), _obj({"n": {"type": "integer"}}))
    assert D.schema_problems(_obj({"n": {}}), _obj({"n": {"type": "string"}}))
    assert D.schema_problems(_obj({"n": {"type": "string"}}), _obj({"n": {}})) == []


def test_facade_mode_still_compares_prompts_because_clients_see_them_either_way():
    old = snap(
        tools=[tool("ddflow_search"), tool("ddflow_call")], prompts=[{"name": "p", "arguments": []}]
    )
    new = snap(tools=[tool("ddflow_search"), tool("ddflow_call")])
    assert D.classify(old, new, "ignores", facade=True).kind == "hard"


def test_a_changed_output_schema_is_judged_from_the_consumers_side():
    def with_out(props):
        t = tool()
        t["outputSchema"] = _obj(props)
        return snap(tools=[t])

    base = with_out({"a": {"type": "string"}})
    wider = with_out({"a": {"type": "string"}, "b": {"type": "string"}})
    changed = with_out({"a": {"type": "integer"}})
    assert D.classify(base, wider, "ignores", schema_grace=False).kind == "soft"
    assert D.classify(base, changed, "ignores", schema_grace=False).kind == "hard"


def test_a_new_surface_is_announced_to_a_client_that_honours_list_changed():
    v = D.classify_surfaces({"flat": snap()}, {"flat": snap(), "core": snap()}, "honours")
    assert v.kind == "soft" and v.announce is True
    quiet = D.classify_surfaces({"flat": snap()}, {"flat": snap(), "core": snap()}, "ignores")
    assert quiet.announce is False
