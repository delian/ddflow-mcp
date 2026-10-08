"""The declarative command registry generates what the hand-written surfaces have.

No command is migrated yet (D-unify 4): these tests declare `Command`s that MATCH existing
commands and show the generators reproduce the existing argparse help and the existing
`tools/list` schema byte for byte. Once that holds for the shapes in use, migrating a
command is deleting its two hand-written halves.
"""

from __future__ import annotations

import argparse

import pytest

from ddflow.api import lifecycle as A_LIFECYCLE
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.parsers._common import GLOBS_HELP, _Globs
from ddflow.surfaces.registry import (
    AS_AGENT,
    Command,
    Param,
    add_commands,
    properties_schema,
)


def _from_tool(name: str, spec: dict) -> Command:
    """A `Command` declaring exactly what a table entry declares."""
    return Command(
        path=(),
        tool=name,
        description=spec["description"],
        params=tuple(
            Param(n, type=t, help=d, required=req) for n, (t, d, req) in spec["properties"].items()
        ),
        identify=bool(spec.get("identify")),
        deprecated=spec.get("deprecated") or {},
    )


def test_command_generates_the_schema_of_every_tool(tmp_path):
    """All tools: the generated `inputSchema` equals what the server's `tools/list` serves
    (pinned byte for byte by tests/golden/test_golden_mcp_tools.py)."""
    reply = Server(tmp_path).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    served = {t["name"]: t for t in reply["result"]["tools"]}
    assert len(served) > 50
    for name, tool in served.items():
        assert _from_tool(name, TOOLS[name]).tool_listing() == tool, name


def test_schema_key_order_is_the_served_order():
    schema = _from_tool("ddflow_claim", TOOLS["ddflow_claim"]).input_schema()
    assert list(schema) == ["type", "properties", "required", "additionalProperties"]
    assert list(schema["properties"]["id"]) == ["type", "description"]
    assert schema["required"] == ["id"]


def test_as_agent_is_on_every_tool_but_identify():
    assert AS_AGENT in _from_tool("ddflow_next", TOOLS["ddflow_next"]).input_schema()["properties"]
    ident = _from_tool("ddflow_identify", TOOLS["ddflow_identify"])
    assert AS_AGENT not in ident.input_schema()["properties"]


def test_array_and_enum_properties():
    p = Param("globs", repeat=True, help="x")
    assert p.type == "array" and p.schema()["items"] == {"type": "string"}
    assert "enum" not in Param("kind", choices=("a", "b")).schema()
    assert Param("kind", choices=("a", "b"), schema_enum=True).schema()["enum"] == ["a", "b"]


def test_command_schema_keeps_a_params_enum():
    """Review finding #1: the schema must come from the Param, not a lossy tuple."""
    cmd = Command(
        path=("x",),
        tool="ddflow_x",
        params=(Param("mode", choices=("a", "b"), schema_enum=True, help="m"),),
    )
    assert cmd.input_schema()["properties"]["mode"]["enum"] == ["a", "b"]
    assert cmd.input_schema()["properties"][AS_AGENT]["type"] == "string"


def test_deprecated_argument_is_accepted_but_not_advertised():
    props = {"a": ("string", "", False), "old": ("string", "", False)}
    assert list(properties_schema(props, deprecated={"old": "a"})["properties"]) == ["a"]


def test_tool_entry_matches_the_table_shape():
    cmd = Command(
        path=("x",),
        tool="ddflow_x",
        description="d",
        params=(Param("id", required=True, help="the id"), Param("cli", cli_only=True)),
        payload=("id",),
        prose=True,
        kind="x",
    )
    entry = cmd.tool_entry()
    assert entry["properties"] == {"id": ("string", "the id", True)}
    assert entry["payload"] == ("id",) and entry["text"] is True and entry["kind"] == "x"
    assert cmd.tool_listing()["name"] == "ddflow_x"


def test_validation():
    with pytest.raises(ValueError):
        Param("a", type="object")
    with pytest.raises(ValueError):
        Param("a", cli_only=True, mcp_only=True)
    with pytest.raises(ValueError):
        Command(path=("x",), params=(Param("a"), Param("a")))
    with pytest.raises(ValueError):
        Command(path=())


def _sub_help(path: tuple[str, ...], parser: argparse.ArgumentParser, monkeypatch) -> str:
    monkeypatch.setenv("COLUMNS", "80")
    for word in path:
        (action,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        parser = action.choices[word]
    return parser.format_help()


def _root():
    return argparse.ArgumentParser(prog="ddflow")


def _generated(cmd: Command, monkeypatch) -> str:
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    return _sub_help(cmd.path, root, monkeypatch)


#: Hand-declared twins of existing commands, covering the argument shapes in use.
RELEASE = Command(
    path=("release",),
    summary="give up a lease",
    params=(Param("id", positional=True), Param("note")),
)
WAIT = Command(
    path=("wait",),
    summary="sleep until an item (or anything) can be claimed; exit 2 = deadline, or waiting "
    "cannot help",
    params=(
        Param("item", default="", help="the item to wait for (default: anything ready)"),
        Param("phase", default="", help="with no --item: anything ready in this phase"),
        Param(
            "kind",
            default=A_LIFECYCLE.DEFAULT_NEXT_KIND,
            choices=("task", "phase"),
        ),
        Param(
            "globs",
            action=_Globs,
            help="with --item: the globs you will claim with, so READY means that claim will "
            "succeed; " + GLOBS_HELP,
        ),
        Param(
            "timeout",
            type="number",
            default=None,
            help=f"seconds to wait (default {A_LIFECYCLE.DEFAULT_WAIT_TIMEOUT_S}; 0 asks without "
            f"waiting)",
        ),
        Param("poll", type="number", default=None, help="seconds between log checks"),
    ),
)
APPROVE = Command(
    path=("approve",),
    summary="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
    params=(
        Param("id", positional=True),
        Param("gate", positional=True),
        Param("note", help="what you looked at, for the record"),
        Param("reject", type="boolean", help="refuse it; --reason required"),
        Param("reason", help="why it was rejected — a 'no' nobody can act on is a stall"),
    ),
)


@pytest.mark.parametrize("cmd", [RELEASE, WAIT, APPROVE], ids=lambda c: c.path[0])
def test_generated_subparser_help_equals_the_hand_written_one(cmd, monkeypatch):
    want = _sub_help(cmd.path, build_parser(), monkeypatch)
    assert _generated(cmd, monkeypatch) == want


def test_generated_subparser_parses_like_the_hand_written_one():
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [WAIT, RELEASE])
    ns = root.parse_args(["wait", "--globs", "a", "--globs", "b", "--timeout", "0"])
    assert ns.globs == ["a", "b"] and ns.timeout == 0.0 and ns.kind == "task" and ns.item == ""
    ns = root.parse_args(["release", "X", "--note", "n"])
    assert (ns.id, ns.note) == ("X", "n")


def test_repeat_boolean_integer_and_required_flags():
    cmd = Command(
        path=("x",),
        params=(
            Param("tag", repeat=True),
            Param("force", type="boolean"),
            Param("n", type="integer", default=3),
            Param("with_dash", required=True, flag="--wd"),
        ),
    )
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    ns = root.parse_args(["x", "--tag", "a", "--tag", "b", "--force", "--n", "5", "--wd", "v"])
    assert (ns.tag, ns.force, ns.n, ns.with_dash) == (["a", "b"], True, 5, "v")
    with pytest.raises(SystemExit):
        root.parse_args(["x"])


def test_group_commands_share_a_parent_parser():
    cmds = [
        Command(path=("grp", "one"), summary="first", params=(Param("id", positional=True),)),
        Command(path=("grp", "two"), summary="second"),
    ]
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), cmds, groups={"grp": "a group"})
    assert root.parse_args(["grp", "one", "I"]).id == "I"
    assert root.parse_args(["grp", "two"]).grp_cmd == "two"


def test_handler_is_attached():
    def fn(a, c): ...

    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [Command(path=("x",), handler=fn)])
    assert root.parse_args(["x"]).fn is fn


def test_a_positional_parameter_is_required_on_both_surfaces():
    """Review finding: argparse refuses a missing positional; the schema must agree."""
    cmd = Command(path=("x",), tool="ddflow_x", params=(Param("file", positional=True),))
    assert cmd.input_schema()["required"] == ["file"]
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    with pytest.raises(SystemExit):
        root.parse_args(["x"])
