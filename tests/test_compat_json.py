"""The contract for machine-readable output (D-compat, D-compat-json-views).

Every tool's result has a declared shape -- an object that will carry a ``schema`` tag, a bare
array that keeps its exact shape, text, or one the call's arguments decide -- and every object
result a JSON Schema generated from the payload field list. ``tests/golden/json/result_schemas.json``
pins them all, so a field added, removed or renamed is a reviewed diff. Re-record on purpose:

    DDFLOW_UPDATE_GOLDEN=1 uv run pytest tests/test_compat_json.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import run_cli

from ddflow.surfaces import registry as R
from ddflow.surfaces.mcp import _OPTIONAL_KEYS, TOOLS, Server

GOLDEN = Path(__file__).parent / "golden" / "json" / "result_schemas.json"


def _table() -> dict:
    return R.result_schemas(TOOLS, _OPTIONAL_KEYS)


def test_the_result_schemas_are_pinned():
    table = json.dumps(_table(), indent=1, sort_keys=False) + "\n"
    if os.environ.get("DDFLOW_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(table, "utf-8")
    assert GOLDEN.read_text("utf-8") == table, (
        "a tool's result schema changed. A field added is fine; one removed or retyped bumps "
        "registry.SCHEMA_VERSIONS. Re-record with DDFLOW_UPDATE_GOLDEN=1."
    )


def test_result_shape_only_returns_declared_shapes():
    seen = {R.result_shape(s.get("payload", ""), text=s.get("text", False)) for s in TOOLS.values()}
    assert seen <= set(R.SHAPES)
    assert seen == set(R.SHAPES), "a shape no tool has is a shape nothing pins"


def test_every_tool_has_one_command_name():
    names = [R.command_name(t) for t in TOOLS]
    assert len(set(names)) == len(names)
    assert all(n and not n.startswith(R.TOOL_PREFIX) for n in names)


def test_no_object_result_declares_a_field_called_schema():
    """The tag is a top-level key of the body; a payload field of the same name would be
    overwritten."""
    for tool, spec in TOOLS.items():
        assert R.SCHEMA_KEY not in R.payload_fields(spec.get("payload", "")), tool


def test_the_tag_is_first_and_only_on_objects():
    assert list(R.tag_body({"b": 1, "a": 2}, "claim")) == ["schema", "b", "a"]
    assert R.tag_body({"b": 1}, "claim")["schema"] == "claim@1"
    for body in ([1], [], "text", None, 3):
        assert R.tag_body(body, "claim") == body
    assert R.tag_body({"b": 1}, "") == {"b": 1}, "no command, no tag"
    # c5c7d9f: a refusal body leads with `refusal` (the block a machine reads first)
    refused = {"refusal": {"exit": 3}, "item": None}
    assert list(R.tag_body(refused, "claim")) == ["refusal", "schema", "item"]
    kept = {"schema": "mine", "x": 1}
    assert R.tag_body(kept, "claim") == kept


def test_a_bumped_version_is_what_the_tag_says(monkeypatch):
    monkeypatch.setitem(R.SCHEMA_VERSIONS, "claim", 2)
    assert R.schema_tag("claim") == "claim@2"
    assert R.output_schema("claim", ("item",))["properties"]["schema"] == {"const": "claim@2"}
    assert R.schema_tag("next") == "next@1"


def test_an_output_schema_is_additive():
    s = R.output_schema("claim", ("item", "holder"), extra=("ci",))
    assert s["additionalProperties"] is True and "required" not in s
    assert list(s["properties"]) == ["schema", "item", "holder", "ci", "refusal"]
    assert R.output_schema("x", "rows") is None  # a bare array
    assert R.output_schema("x", "text", text=True) is None
    assert R.output_schema("x", lambda a: "rows") is None
    assert list(R.output_schema("x", "")["properties"]) == ["schema", "refusal"]
    assert R.array_schema("x", "rows")["title"] == "x@1"
    assert R.array_schema("x", ("a",)) is None


#: Tools whose call touches a companion, the network or a test runner: not part of a shape check.
_NOT_RUN = {
    "ddflow_companions",
    "ddflow_bisect",
    "ddflow_import_verify",
    "ddflow_ci",
    "ddflow_verify",
    "ddflow_pins",
    "ddflow_tests",
    "ddflow_precommit",
    "ddflow_reviewers_list",
    "ddflow_onboard",
    "ddflow_export",
    "ddflow_rebuild",
    "ddflow_wait",
}


def test_the_declared_shape_is_the_shape_a_call_returns(repo):
    """Run the read tools the wire-shape table knows and compare each body with its declared
    shape: an array is an array, an object has exactly its projection's fields."""
    from test_api_layer import MIGRATED_WIRE_SHAPES

    for argv in (
        ("init",),
        ("phase", "add", "P1", "--title", "P"),
        ("task", "add", "T1", "--phase", "P1", "--globs", "a.py"),
        (
            "decision",
            "add",
            "--id",
            "D1",
            "--title",
            "D",
            "--decision",
            "use it",
            "--globs",
            "a.py",
        ),
    ):
        code, out, err = run_cli(repo, *argv)
        assert code == 0, (argv, out, err)
    server = Server(repo)
    seen = {"array": 0, "object": 0}
    for tool, (_argv, arguments) in sorted(MIGRATED_WIRE_SHAPES.items()):
        spec = TOOLS[tool]
        shape = R.result_shape(spec.get("payload", ""), text=spec.get("text", False))
        if tool in _NOT_RUN or shape not in seen:
            continue
        reply = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        body = json.loads(reply["result"]["content"][0]["text"])
        seen[shape] += 1
        if shape == "array":
            assert isinstance(body, list), (tool, body)
            continue
        assert body is None or isinstance(body, dict), (tool, type(body))
        if isinstance(body, dict):
            assert body.get(R.SCHEMA_KEY) == R.schema_tag(R.command_name(tool)), (tool, body)
        fields = R.payload_fields(spec.get("payload", ""))
        if fields and isinstance(body, dict):
            extra = {R.REFUSAL_KEY, R.SCHEMA_KEY, *_OPTIONAL_KEYS}
            got = set(body) - extra
            # a refused or failed call leads with `refusal` and leaves unset fields out
            ok = got <= set(fields) if R.REFUSAL_KEY in body else got == set(fields)
            assert ok, (tool, sorted(body))
    assert all(seen.values()), seen


# -- the one emitter (B-uni-cmd-migrate (a)) ---------------------------------------------


def test_emit_json_is_the_bytes_every_command_printed(capsys):
    import datetime
    import json as _json

    from ddflow.surfaces.render import emit_json

    body = {"a": [1, 2], "when": datetime.date(2026, 10, 8), "path": Path("/x"), "n": None}
    emit_json(body)
    assert capsys.readouterr().out == _json.dumps(body, indent=2, default=str) + "\n"
    emit_json([], file=None)
    assert capsys.readouterr().out == "[]\n"


def test_no_command_module_prints_its_own_json():
    import ast

    root = Path(__file__).parents[1] / "ddflow" / "surfaces"
    own = []
    scanned = sorted([*(root / "commands").glob("*.py"), root / "context.py", root / "cli.py"])
    assert len(scanned) > 20, "the scan found no command modules: the guard would pass vacuously"
    for path in scanned:
        tree = ast.parse(path.read_text("utf-8"))
        if any(
            (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "dumps"
            )
            or (
                isinstance(n, ast.ImportFrom)
                and n.module == "json"
                and any(a.name == "dumps" for a in n.names)
            )
            for n in ast.walk(tree)
        ):
            own.append(path.name)
    assert not own, f"modules printing their own json.dumps instead of render.emit_json: {own}"


# -- both surfaces name a result alike ----------------------------------------------------


def _leaf_paths(parser) -> list[tuple[str, ...]]:
    import argparse

    out: list[tuple[str, ...]] = []

    def walk(node, path):
        subs = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            out.append(path)
            return
        for word, child in subs[0].choices.items():
            walk(child, (*path, word))

    walk(parser, ())
    return out


def test_every_cli_command_is_named_by_the_tool_that_serves_it():
    from ddflow.surfaces.cli import build_parser
    from ddflow.surfaces.exemptions import COVERING_TOOLS, EXEMPT_PATHS, EXEMPT_WORDS, ROUTED_PATHS

    tools = {R.command_name(t) for t in TOOLS}
    paths = _leaf_paths(build_parser())
    assert len(paths) > 80
    unnamed = [
        p
        for p in paths
        if R.command_for_path(p, ROUTED_PATHS, COVERING_TOOLS) not in tools
        and p not in EXEMPT_PATHS
        and p[0] not in EXEMPT_WORDS
    ]
    assert not unnamed, f"CLI commands whose --json schema name no tool carries: {unnamed}"


def test_parsed_path_resolves_an_alias_to_its_command():
    from ddflow.surfaces.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["gate", "status", "T1"])
    assert R.parsed_path(parser, args) == ("gate", "status")
    assert R.command_for_path(("gate", "status"), {}, {}) == "gate_status"
    assert R.command_for_path(("bug", "file-tasks"), {}, {}) == "bug_file_tasks"
