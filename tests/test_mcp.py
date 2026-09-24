"""MCP is a second door onto one implementation — these tests pin that it stays so."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard.mcp_server import TOOLS, _schema, serve


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(
        repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--globs", "src/auth/*"
    )
    return repo


def rpc(repo, messages):
    inp = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    out = io.StringIO()
    serve(repo, stdin=inp, stdout=out)
    return [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]


def test_handshake_echoes_a_known_protocol(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        ],
    )
    assert r[0]["result"]["protocolVersion"] == "2025-03-26"


def test_handshake_falls_back_for_an_unknown_protocol(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        ],
    )
    assert r[0]["result"]["protocolVersion"] in ("2025-06-18",)


def test_every_tool_has_a_valid_schema_and_a_real_description():
    for name, spec in TOOLS.items():
        schema = _schema(spec)
        assert schema["type"] == "object"
        assert len(spec["description"]) > 40, f"{name} needs a usable description"
        for prop, body in schema["properties"].items():
            assert body["type"] in ("string", "integer", "boolean", "number")
            assert body["description"], f"{name}.{prop} is undocumented"


def test_tools_list_matches_the_registry(proj):
    r = rpc(proj, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert {t["name"] for t in r[0]["result"]["tools"]} == set(TOOLS)


def test_a_tool_call_returns_the_cli_result(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_next", "arguments": {"phase": "P1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["ready"][0]["id"] == "P1.T1"


def test_nothing_to_do_is_a_result_not_an_error(proj):
    """Exit 2 must reach the model as readable content, never as a transport error —
    an agent that sees an error retries; an agent that sees 'nothing ready' moves on."""
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="other")
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_next", "arguments": {"phase": "P1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["isError"] is False
    assert res["_meta"]["exit"] == 2


def test_a_refused_claim_reaches_the_model_as_content(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="holder")
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_claim", "arguments": {"id": "P1.T1"}},
            }
        ],
    )
    res = r[0]["result"]
    assert res["_meta"]["exit"] == 3
    assert "held by" in res["content"][0]["text"]


def test_unknown_tool_is_an_error_result_listing_alternatives(proj):
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "orchard_teleport", "arguments": {}},
            }
        ],
    )
    assert r[0]["result"]["isError"] is True
    assert "orchard_brief" in r[0]["result"]["content"][0]["text"]


def test_notifications_get_no_reply(proj):
    r = rpc(proj, [{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert r == [], "a notification must not produce a response frame"


def test_malformed_json_gets_a_parse_error_not_a_crash(proj):
    out = io.StringIO()
    serve(proj, stdin=io.StringIO("{not json\n"), stdout=out)
    assert json.loads(out.getvalue())["error"]["code"] == -32700


def test_resources_are_readable(proj):
    r = rpc(
        proj,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": "orchard://board"},
            },
        ],
    )
    assert len(r[0]["result"]["resources"]) >= 3
    assert "P1" in r[1]["result"]["contents"][0]["text"]


def test_server_writes_nothing_but_frames_to_stdout(proj):
    """A stray print corrupts the stream and the client sees a hung server."""
    out = io.StringIO()
    serve(
        proj,
        stdin=io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "orchard_board", "arguments": {}},
                }
            )
            + "\n"
        ),
        stdout=out,
    )
    for line in out.getvalue().splitlines():
        json.loads(line)  # every line must be a valid JSON-RPC frame
