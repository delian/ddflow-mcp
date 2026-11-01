"""The application layer: wire-shape parity between the CLI and MCP surfaces.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from api_layer_support import (
    FAIL,
    MIGRATED_WIRE_SHAPES,
    NOTHING,
    TEXT_BODIED,
    WRITES_NOT_COMPARABLE,
    _surfaces_agree,
)
from conftest import run_cli

from ddflow.surfaces.mcp import TOOLS


@pytest.mark.parametrize("tool", sorted(MIGRATED_WIRE_SHAPES))
def test_a_migrated_tool_reproduces_its_CLI_json_exactly(repo, tool):
    """One test for every migration, present and future.

    Compares the two payloads WITHOUT indexing into either. The bespoke version of this
    reached for `["findings"]` on the MCP side, which validated the contents while
    accommodating the exact shape change it was written to prevent -- so the
    generalisation is not tidiness, it is the thing that makes the check honest.
    """
    import json as _json

    # A real `ddflow mcp` runs through cli.main, which registers the command table `doctor`
    # checks references against (Bc782b6c724); an in-process server needs the import too.
    import ddflow.surfaces.cli  # noqa: F401
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--phase", "P1", "--globs", "a.py")
    run_cli(repo, "task", "add", "A", "--title", "A fixture", "--needs", "B")
    run_cli(
        repo, "task", "add", "B", "--title", "B fixture", "--needs", "A"
    )  # a cycle, so findings exist
    # A decision with an EXPLICIT id, governing T1's globs, so the decision tools have
    # something to return. An auto id would be content-addressed and unrepeatable, and
    # a comparison of two empty bodies passes without proving anything.
    run_cli(
        repo,
        "decision",
        "add",
        "--id",
        "D1",
        "--title",
        "D",
        "--decision",
        "use the typed layer",
        "--globs",
        "a.py",
    )
    # A rule with an explicit id, so the rule tools have a row to compare, not two empties.
    run_cli(
        repo, "rule", "add", "--id", "r-test", "--title", "Test rule", "--content", "test content"
    )
    # A fixed probe result for every companion, so neither surface shells out to detect
    # one. Each probe runs the real tool (npx, docker, ...) with a timeout, and two live
    # probes need not agree: under load one times out (`unknown`) while the other
    # answers (`installed`), and the comparison fails on the machine, not on a surface.
    # `flipflop` makes that disagreement certain instead of load-dependent. Its probe
    # cannot be spawned during the CLI call (no interpreter line: exec format error, the
    # same "could not tell" a timeout gives, and likewise never cached) and answers
    # during the MCP call, so a test that lets the surfaces probe live fails every run.
    from ddflow.services import companions

    probe = repo / "flipflop-probe"
    probe.write_text("not a program\n", "utf-8")
    probe.chmod(0o755)
    (repo / ".ddflow" / "companions.toml").write_text(
        f'[[companion]]\nid = "flipflop"\nkind = "cli"\ndetect = ["{probe}"]\n', "utf-8"
    )
    companions._write_cache(
        repo, {c.id: (c.id != "optmem", f"fixed for {c.id}") for c in companions.load(repo)}
    )

    argv, arguments = MIGRATED_WIRE_SHAPES[tool]
    # `--json` is passed for the JSON tools only. A text-bodied tool ignores it today,
    # but passing it would encode "these are the same kind of thing" into the check that
    # exists to tell them apart.
    head = [] if tool in TEXT_BODIED else ["--json"]
    _code, cli_out, _err = run_cli(repo, *head, *argv)
    from_cli = None if tool in TEXT_BODIED else _json.loads(cli_out)
    probe.write_text("#!/bin/sh\nexit 0\n", "utf-8")  # flipflop now answers: see above

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    text = reply["result"]["content"][0]["text"]

    if tool in TEXT_BODIED:
        # Document against document. `run_cli` strips nothing, so the only permitted
        # difference is the trailing newline `print` adds.
        assert text.rstrip("\n") == cli_out.rstrip("\n"), (
            f"{tool}: the two surfaces render different documents\n"
            f"CLI: {cli_out[:300]!r}\nMCP: {text[:300]!r}"
        )
        assert text.strip(), f"{tool} returned an empty document"
        return

    from_mcp_text = text
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    assert start != -1, f"{tool} returned no JSON body: {from_mcp_text[:200]}"
    from_mcp = _json.loads(text[start:])

    assert type(from_mcp) is type(from_cli), (
        f"{tool}: the MCP body is a {type(from_mcp).__name__} and the CLI's is a "
        f"{type(from_cli).__name__} — a migration changed the wire shape"
    )
    # The five bounded reads (B-mcp-payload-bound) are the deliberate exception: the MCP
    # body is the CLI's body put through `mcp_bound`, and nothing else. Their cuts are
    # pinned by tests/test_mcp_payload_bound.py.
    from ddflow.surfaces.mcp_bound import BOUNDS

    if tool in BOUNDS:
        # MCP tags the bounded body; the bound itself works on the untagged one
        tag = from_cli.pop("schema", None) if isinstance(from_cli, dict) else None
        from_cli = BOUNDS[tool](from_cli, arguments)[0]
        if tag and isinstance(from_cli, dict):
            from_cli = {"schema": tag, **from_cli}
    assert _surfaces_agree(tool, from_mcp, from_cli), (
        f"{tool}: the surfaces disagree\nCLI: {from_cli}\nMCP: {from_mcp}"
    )


def test_every_typed_tool_has_a_wire_shape_row():
    """A migration without a row is a migration nothing checks. The map is the ratchet:
    `ARGV_TOOLS_CEILING` counts what is left, this covers what has moved."""
    typed = {n for n, s in TOOLS.items() if "api" in s}
    unchecked = typed - set(MIGRATED_WIRE_SHAPES) - WRITES_NOT_COMPARABLE
    assert not unchecked, (
        f"migrated with no wire-shape row: {sorted(unchecked)}. Add one to "
        f"MIGRATED_WIRE_SHAPES so the contract is checked."
    )


def test_a_measured_field_cannot_make_the_surfaces_disagree():
    """Bug Ba0904a2430: the parity COMPARISON (not just the helper) must ignore measured
    values while keeping every key and the value's type, so a real disagreement still
    fails. It calls `_surfaces_agree`, the same function the migrated-tool test runs."""
    one = {"runs": [{"tests": 1, "outcome": "could_not_run", "seconds": 0.0}]}
    two = {"runs": [{"tests": 1, "outcome": "could_not_run", "seconds": 0.01}]}
    assert _surfaces_agree("ddflow_bisect", one, two), "0.0 and 0.01 differ only by clock"
    extra = {"runs": [{"tests": 1, "outcome": "could_not_run", "detail": "x", "seconds": 0.01}]}
    assert not _surfaces_agree("ddflow_bisect", one, extra), "a real field difference remains"
    missing = {"runs": []}
    assert not _surfaces_agree("ddflow_bisect", one, missing), "a missing run remains"
    none = {"runs": [{"tests": 1, "outcome": "could_not_run", "seconds": None}]}
    assert not _surfaces_agree("ddflow_bisect", one, none), "a type difference remains"
    assert not _surfaces_agree("ddflow_other", one, two), "only the measuring tools mask"


def test_a_migrated_tool_gives_both_surfaces_the_SAME_data(repo):
    """The property the typed layer exists for, asserted rather than assumed.

    `cmd_loops` used to build its JSON body and its human paragraph independently — two
    renderings of one answer, kept in step by hand. That is the shape that printed a
    coverage gap to humans only, invisible to the agent reading JSON that most needed
    it. Now both derive from one `Outcome`, and this checks they have not drifted apart
    again.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "A fixture", "--needs", "B")
    run_cli(
        repo, "task", "add", "B", "--title", "B fixture", "--needs", "A"
    )  # a real cycle, so findings exist

    _code, cli_out, _err = run_cli(repo, "--json", "loops")
    from_cli = _json.loads(cli_out)
    assert from_cli, "no findings at all; this test would prove nothing"

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_loops", "arguments": {}},
        }
    )
    text = reply["result"]["content"][0]["text"]
    # Parsed WITHOUT reaching into a key. The first version indexed `["findings"]`, which
    # validated the contents while missing that the top-level SHAPE had changed from an
    # array to an object -- the exact break it was written to prevent, hidden by the
    # test's own accommodation of it.
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    from_mcp = _json.loads(text[start:])
    assert isinstance(from_mcp, list), f"the MCP body is no longer an array: {text[:200]}"
    assert from_mcp == from_cli, (
        f"the two surfaces disagree about the same answer:\nCLI: {from_cli}\nMCP: {from_mcp}"
    )


def test_a_migrated_tool_keeps_its_exit_contract(repo):
    """Migration must not silently renumber exit codes. `loops` returns 1 when there
    are findings and 2 when there are none — callers branch on that, and "loops found"
    being a 1 rather than a 0 is a contract this layer inherits rather than redesigns.
    """
    run_cli(repo, "init")
    assert run_cli(repo, "loops")[0] == NOTHING

    run_cli(repo, "task", "add", "A", "--title", "A fixture", "--needs", "B")
    run_cli(repo, "task", "add", "B", "--title", "B fixture", "--needs", "A")
    assert run_cli(repo, "loops")[0] == FAIL
