"""B25ae7cf604: a rule tool's schema must not advertise an input its call discards.

`ddflow_rule_list` declared `limit` and `json`, and `ddflow_rule_remove` declared `reason`
("Why it is removed (recorded)") while describing the removal as "logged as an event". None
of them reached the api -- `api.rule_list` and `api.rule_remove` take no such parameter, and
rules are filesystem configuration that is never written to the event log. A caller's
`limit` was ignored and its removal reason silently dropped. An input the call cannot honour
is not advertised, so passing one is refused as an unknown argument instead.

Bf84a50bce3 (D-compat) changed the other half: an argument a RELEASE accepted (0.1.15 took
all three) keeps being accepted until 1.0, as a `deprecated` no-op that is hidden from
`tools/list` and answered with a deprecation note -- never silently dropped. Everything else
declared must still reach the call.
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
    declared = set(TOOLS[tool]["properties"]) - set(TOOLS[tool].get("deprecated") or {})
    assert declared <= _forwarded(tool), (
        f"{tool} advertises {sorted(declared - _forwarded(tool))} but never passes them on"
    )


def test_rule_remove_does_not_claim_an_event_it_never_writes():
    assert "event" not in TOOLS["ddflow_rule_remove"]["description"].lower()


def test_a_discarded_input_is_said_to_be_deprecated_not_dropped_silently(tmp_path):
    from ddflow.surfaces.mcp import Server

    def call(args):
        return Server(tmp_path, agent="t").handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_rule_remove", "arguments": args},
            }
        )["result"]

    deprecated = call({"id": "r-x", "reason": "why"})
    assert any("deprecated" in c["text"] and "reason" in c["text"] for c in deprecated["content"])
    # an argument that never existed is still refused
    res = call({"id": "r-x", "nonsense": "why"})
    assert res.get("isError") and "unknown argument" in res["content"][0]["text"], res
