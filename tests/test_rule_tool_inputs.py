"""B25ae7cf604: a rule tool's schema must not advertise an input its call discards.

`ddflow_rule_list` declared `limit` and `json`, and `ddflow_rule_remove` declared `reason`
("Why it is removed (recorded)") while describing the removal as "logged as an event". None
of them reached the api -- `api.rule_list` and `api.rule_remove` take no such parameter, and
rules are filesystem configuration that is never written to the event log. A caller's
`limit` was ignored and its removal reason silently dropped. An input the call cannot honour
is not advertised, so passing one is refused as an unknown argument instead.
"""

from __future__ import annotations

import pytest

from ddflow.surfaces.mcp import TOOLS

TOOLS_CHECKED = ("ddflow_rule_list", "ddflow_rule_remove")


def _forwarded(tool: str) -> set[str]:
    """Every string constant the tool's `api` lambda holds: the argument keys it reads."""
    code = TOOLS[tool]["api"].__code__
    return {c for c in code.co_consts if isinstance(c, str)}


@pytest.mark.parametrize("tool", TOOLS_CHECKED)
def test_every_declared_input_reaches_the_call(tool):
    declared = set(TOOLS[tool]["properties"])
    assert declared <= _forwarded(tool), (
        f"{tool} advertises {sorted(declared - _forwarded(tool))} but never passes them on"
    )


def test_rule_remove_does_not_claim_an_event_it_never_writes():
    assert "event" not in TOOLS["ddflow_rule_remove"]["description"].lower()


def test_a_discarded_input_is_refused_not_dropped(tmp_path):
    from ddflow.surfaces.mcp import Server

    r = Server(tmp_path, agent="t").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_rule_remove", "arguments": {"id": "r-x", "reason": "why"}},
        }
    )
    res = r["result"]
    assert res.get("isError") and "unknown argument" in res["content"][0]["text"], res
