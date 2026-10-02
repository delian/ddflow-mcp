"""The MCP tool list is paid for in every agent's context on every session, so its size is
ratcheted. Measured before the trim: 90 tools, 119,179 bytes compact. The shared `as_agent`
/ `relation` / `check_only` descriptions were repeated in full on 89 / 7 / 7 tools; the full
text now lives once (ddflow_identify, ddflow_help and the handshake instructions)."""

from __future__ import annotations

import json

from ddflow.surfaces.mcp import ADD_TOOLS, TOOLS, Server, _schema

#: Compact `tools/list` bytes. Raise only with a reason in the commit; lowering is welcome.
TOOLS_LIST_BUDGET = 92_000
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
    assert ADD_TOOLS  # the add tools still carry the answer arguments
