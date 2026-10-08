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
from ddflow.surfaces import registry as R
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.exemptions import EXEMPTIONS
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
    cmd = Command(
        path=("x",),
        tool="ddflow_x",
        params=(Param("file", positional=True), Param("other", positional=True)),
    )
    assert cmd.input_schema()["required"] == ["file", "other"]
    with pytest.raises(ValueError):
        Param("p", positional=True, default="y")
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    with pytest.raises(SystemExit):
        root.parse_args(["x"])


# -- shapes the migration needed ---------------------------------------------------------


def test_an_optional_positional_is_optional_on_both_surfaces():
    cmd = Command(
        path=("x",),
        tool="ddflow_x",
        params=(
            Param("verb", positional=True, nargs="?", choices=("add",), cli_only=True),
            Param("session", positional=True, nargs="?", default=""),
        ),
    )
    assert "required" not in cmd.input_schema()
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    ns = root.parse_args(["x"])
    assert (ns.verb, ns.session) == (None, "")
    assert root.parse_args(["x", "add", "S"]).session == "S"
    with pytest.raises(ValueError, match="nargs is for a positional"):
        Param("p", nargs="?")


def test_a_tool_may_require_what_the_flag_does_not():
    """`session note --text` is read from stdin when absent; the tool has no stdin."""
    cmd = Command(path=("x",), tool="ddflow_x", params=(Param("text", tool_required=True),))
    assert cmd.input_schema()["required"] == ["text"]
    assert cmd.properties()["text"][2] is True
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    assert root.parse_args(["x"]).text is None


def test_exclusive_parameters_share_one_group_and_metavar_is_kept():
    cmd = Command(
        path=("x",),
        params=(
            Param("new", type="boolean", exclusive="one"),
            Param("extends", default="", metavar="ID", exclusive="one"),
            Param("other"),
        ),
    )
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    assert root.parse_args(["x", "--extends", "A"]).extends == "A"
    with pytest.raises(SystemExit):
        root.parse_args(["x", "--new", "--extends", "A"])
    sub = next(a for a in root._actions if isinstance(a, argparse._SubParsersAction)).choices["x"]
    assert "[--new | --extends ID]" in " ".join(sub.format_usage().split())


def test_tool_order_is_the_order_of_the_schema_not_of_the_help():
    cmd = Command(
        path=("x",),
        tool="ddflow_x",
        params=(Param("a"), Param("b"), Param("only_cli", cli_only=True), Param("c")),
        tool_order=("c", "a", "b"),
    )
    assert list(cmd.properties()) == ["c", "a", "b"]
    assert list(cmd.input_schema()["properties"])[:3] == ["c", "a", "b"]
    root = _root()
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    sub = next(a for a in root._actions if isinstance(a, argparse._SubParsersAction)).choices["x"]
    assert [a.dest for a in sub._actions][1:] == ["a", "b", "only_cli", "c"]
    with pytest.raises(ValueError, match="tool_order must name each tool parameter once"):
        Command(path=("x",), tool="t", params=(Param("a"), Param("b")), tool_order=("a",))


def test_defaults_ride_on_the_parse_and_handlers_may_be_supplied_apart():
    def fn(a, c): ...

    cmd = Command(path=("grp", "list"), defaults={"list_kind": "task"})
    root = _root()
    add_commands(
        root.add_subparsers(dest="cmd", required=True), [cmd], handlers={("grp", "list"): fn}
    )
    ns = root.parse_args(["grp", "list"])
    assert (ns.list_kind, ns.fn) == ("task", fn)


def test_a_group_is_listed_only_when_it_has_help_and_an_existing_group_is_joined():
    root = _root()
    subs = root.add_subparsers(dest="cmd", required=True)
    add_commands(
        subs,
        [Command(path=("quiet", "one")), Command(path=("loud", "one"))],
        groups={"loud": "has a line"},
    )
    help_ = root.format_help()
    assert "has a line" in help_ and "quiet" in help_.split("{")[1]  # in the choices...
    assert [c.dest for c in subs._choices_actions] == ["loud"]  # ...but only `loud` has a row
    add_commands(subs, [Command(path=("quiet", "two"))])  # joins the group made before
    assert root.parse_args(["quiet", "two"]).quiet_cmd == "two"
    assert root.parse_args(["quiet", "one"]).quiet_cmd == "one"


# -- the parity exemptions are fields --------------------------------------------------


def test_exemption_derivations():
    cmds = (
        Command(path=("mcp",), reason="r" * 30),
        Command(path=("init",), reason="r" * 30, via=("ddflow_setup",)),
        Command(path=("task", "list"), via=("ddflow_list", "task")),
        Command(path=("search",), reason="r" * 30, via=("ddflow_list", "search")),
        Command(path=(), tool="ddflow_ci", flag_exempt={"--sha": "why"}),
        Command(path=(), tool="ddflow_brief", prose=True, prose_reason="text"),
    )
    assert set(R.exempt_paths(cmds)) == {("mcp",), ("init",)}
    assert R.routed_paths(cmds) == {
        ("task", "list"): ("ddflow_list", "task"),
        ("search",): ("ddflow_list", "search"),
    }
    assert R.covering_tools(cmds) == {"init": ("ddflow_setup",)}
    assert R.declared_words(cmds) == {"mcp"}
    assert R.flag_exemptions(cmds) == {("ddflow_ci", "--sha"): "why"}
    assert R.prose_reasons(cmds) == {"ddflow_brief": "text"}
    with pytest.raises(ValueError):
        Command(path=("x",), via=("a", "b", "c"))


def test_every_declared_exemption_is_well_formed():
    paths = [c.path for c in EXEMPTIONS if c.path]
    assert len(paths) == len(set(paths)), "a command is declared twice"
    for c in EXEMPTIONS:
        if c.prose:
            assert len(c.prose_reason) > 20, f"{c.tool}: a prose tool needs its reason"
        for flag, reason in c.flag_exempt.items():
            assert flag.startswith("--") and reason, (c.tool, flag)
        if c.path and not (c.reason or c.via):
            raise AssertionError(f"{c.path}: neither a reason nor a route")
