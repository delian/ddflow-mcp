"""A refused or failed MCP call leads with WHY, never with the success shape in nulls.

Bug B9cf58aaeaa: a refused `ddflow_claim` answered `content[0]` = `{"item": null,
"holder": null, "worktree": null, ...}` with the reason only in a second block, and the
data the refusal carried (the alternatives it names) projected away. A machine reads
`content[0]`; it saw a broken success.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.core import outcome as O
from ddflow.surfaces.mcp import TOOLS, Server, _outcome_result


def _first(res: dict) -> object:
    return json.loads(res["content"][0]["text"])


def test_a_refused_claim_leads_with_the_refusal_and_its_payload(repo):
    """The reported case, end to end through the server."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "a.py")
    code, _out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="alpha")
    assert code == 0, err

    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_claim",
                "arguments": {"id": "T2", "no_worktree": True, "as_agent": "beta"},
            },
        }
    )
    res = reply["result"]
    assert res["_meta"]["exit"] == O.REFUSED
    assert res["isError"] is False
    body = _first(res)
    assert isinstance(body, dict), body
    assert next(iter(body)) == "refusal", f"does not lead with the refusal: {body}"
    assert body["refusal"]["outcome"] == "refused"
    assert body["refusal"]["exit"] == O.REFUSED
    assert "overlaps" in body["refusal"]["reason"], body
    # Not the success schema padded with nulls ...
    assert "holder" not in body and "worktree" not in body, body
    assert None not in body.values(), body
    # ... but what the refusal DID say, which the projection used to drop.
    assert body["id"] == "T2"
    assert "alternatives" in body, body


TUPLE_TOOLS = sorted(n for n, s in TOOLS.items() if isinstance(s.get("payload"), tuple))


@pytest.mark.parametrize("tool", TUPLE_TOOLS)
@pytest.mark.parametrize("exit_code", [O.FAIL, O.REFUSED])
def test_no_tool_answers_a_refusal_with_its_success_shape_in_nulls(tool, exit_code):
    """Every tool with a projected body, not just claim: the chokepoint is shared, and
    a refusal usually carries less data than the success it replaced."""
    payload = TOOLS[tool]["payload"]
    out = O.Outcome(kind="k", data={"candidates": [{"id": "X1"}]}, exit=exit_code, reason="why not")
    body = _first(_outcome_result(out, payload))
    assert next(iter(body)) == "refusal", f"{tool}: {body}"
    assert body["refusal"] == {
        "reason": "why not",
        "outcome": O.EXIT_NAMES[exit_code],
        "exit": exit_code,
    }
    assert next(iter(body["refusal"])) == "reason", body
    assert None not in body.values(), f"{tool} padded its refusal with nulls: {body}"
    assert body["candidates"] == [{"id": "X1"}], f"{tool} dropped the payload: {body}"


def test_a_padded_nothing_leads_with_its_reason_and_keeps_its_shape():
    """`heartbeat` with no lease is exit 2 and used to be all nulls. It keeps its keys
    (nothing-to-do is that tool's answer) but the reason comes first."""
    out = O.nothing("lease.renewed", "no lease held T1")
    body = _first(_outcome_result(out, ("renewed", "waiters", "globs_withheld")))
    assert list(body) == ["refusal", "renewed", "waiters", "globs_withheld"], body
    assert body["refusal"]["reason"] == "no lease held T1"


def test_a_complete_nothing_and_every_array_keep_their_shape():
    """Exit 2 with its full shape is a RESULT, byte-identical to the CLI's `--json`;
    an array (loops exits 1 WITH its findings) stays an array."""
    full = O.nothing("next", "nothing ready", ready=[], blocked=[])
    assert _first(_outcome_result(full)) == {"ready": [], "blocked": []}
    loops = O.failed("loops", "1 finding(s)", findings=[{"x": 1}])
    assert _first(_outcome_result(loops, "findings")) == [{"x": 1}]
    assert _outcome_result(loops, "findings")["content"][1]["text"] == "1 finding(s)"
    # A failure that filled its declared shape is an answer too: `gate verify` fails
    # WITH its results, and `reason` is its own wire field there.
    verify = ("gate", "reason", "results", "verified")
    failed = O.Outcome(
        kind="gate.verify",
        data={"gate": "g", "reason": "no command", "results": [], "verified": False},
        exit=O.FAIL,
        reason="no command",
    )
    assert _first(_outcome_result(failed, verify)) == {
        "gate": "g",
        "reason": "no command",
        "results": [],
        "verified": False,
    }


def test_a_whole_data_refusal_leads_too():
    """A tool whose body is its whole `data` is not padded, but a refusal is never a
    result: exit 3 always leads with why."""
    out = O.refused("k", "lease held by beta", holder="beta")
    body = _first(_outcome_result(out))
    assert list(body) == ["refusal", "holder"], body
    assert body["refusal"]["reason"] == "lease held by beta"


def test_a_successful_call_is_untouched():
    ok = O.ok("item.claimed", item="T1", holder="a", worktree=None)
    assert _first(_outcome_result(ok, ("item", "holder", "worktree"))) == {
        "item": "T1",
        "holder": "a",
        "worktree": None,
    }
