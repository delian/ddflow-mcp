"""The MCP tool list is paid for in every agent's context on every session, so its size is
ratcheted. Measured before the trim: 90 tools, 119,179 bytes compact. The shared `as_agent`
/ `relation` / `check_only` descriptions were repeated in full on 89 / 7 / 7 tools; the full
text now lives once (ddflow_identify, ddflow_help and the handshake instructions)."""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.surfaces import mcp as mcp_module
from ddflow.surfaces.mcp import ADD_TOOLS, TOOLS, Server, _schema

#: Compact `tools/list` bytes. Raise only with a reason in the commit; lowering is welcome.
# 93_500: ddflow_export (B-export-surfaces) is ~1.4 KB, 15 arguments because the CLI/MCP flag
# parity ratchet needs every `ddflow export` filter flag reachable; main sat at 91,958.
# 91_500: ddflow_list (B-view-mcp-list) is ONE tool for the six viewers (~1.3 KB); the
# shared `as_agent` description, repeated on every tool, went from 113 to 66 characters (-4 KB).
# 93_500 (raised, B-dupes-sweep): ddflow_dupes + ddflow_link settle near-duplicate pairs
# already in the log; together ~1.3 KB. The cheaper alternative (one tool with a mode
# argument) would still carry most of the text and need the same flag exemptions.
TOOLS_LIST_BUDGET = 93_500
SHARED_DESCRIPTION_MAX = 200


def _tools_list(repo) -> list[dict]:
    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return reply["result"]["tools"]


def test_compact_tools_list_stays_within_budget(repo):
    size = len(json.dumps({"tools": _tools_list(repo)}, separators=(",", ":")))
    assert size <= TOOLS_LIST_BUDGET, (
        f"tools/list is {size} bytes (budget {TOOLS_LIST_BUDGET}): shorten a description "
        "instead of raising the budget"
    )


def test_shared_parameter_descriptions_are_not_repeated_in_full():
    long_ones = []
    for name, spec in TOOLS.items():
        props = _schema(spec)["properties"]
        for arg in ("as_agent", "relation", "check_only"):
            if arg in props and len(props[arg]["description"]) > SHARED_DESCRIPTION_MAX:
                long_ones.append((name, arg))
    assert not long_ones, long_ones
    # ...and the add tools still carry the answer arguments at all.
    for name in ADD_TOOLS:
        props = _schema(TOOLS[name])["properties"]
        assert "relation" in props and "check_only" in props, name


def test_the_trimmed_text_still_has_a_home():
    """Shortening must not delete the semantics: `as_agent` is explained on
    ddflow_identify, and the relation values in the handshake instructions."""
    ident = TOOLS["ddflow_identify"]["description"]
    assert "as_agent" in ident and "--agent" in ident
    template = Path(mcp_module.__file__).parent.parent / "templates/prompts/mcp_instructions.md"
    text = template.read_text()
    for value in ("extends:ID", "duplicate_of:ID", "related:ID"):
        assert value in text, value


def test_ddflow_loops_names_the_repeated_failure_detector():
    """B1d72a8144c: repeated_failure was missing from the tools/list description."""
    assert "repeated_failure" in TOOLS["ddflow_loops"]["description"]
