"""`[mcp].output_schemas` (D-compat-json-views): an `outputSchema` per object tool and the
result also as `structuredContent`, for the revisions that have them; off by default."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.surfaces import registry as R
from ddflow.surfaces.mcp import _OPTIONAL_KEYS, TOOLS, Server

MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _rpc(server: Server, method: str, params: dict | None = None, mid: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)["result"]


def _server(repo: Path, version: str | None) -> Server:
    server = Server(repo)
    if version:
        _rpc(server, "initialize", {"protocolVersion": version, "capabilities": {}})
    return server


def _tools(server: Server, **extra) -> dict[str, dict]:
    res = _rpc(server, "tools/list", extra or None)
    return {t["name"]: t for t in res["tools"]}


@pytest.fixture
def on(monkeypatch, repo):
    monkeypatch.setenv("DDFLOW_MCP_OUTPUT_SCHEMAS", "on")
    run_cli(repo, "init")
    return repo


def test_off_by_default_nothing_is_declared(repo):
    run_cli(repo, "init")
    server = _server(repo, "2025-11-25")
    assert not any("outputSchema" in t or "_meta" in t for t in _tools(server).values())
    res = _rpc(server, "tools/call", {"name": "ddflow_status", "arguments": {}})
    assert "structuredContent" not in res


def test_each_object_tool_declares_the_schema_the_registry_generates(on):
    tools = _tools(_server(on, "2025-11-25"))
    expected = R.result_schemas(TOOLS, _OPTIONAL_KEYS)
    declared = 0
    for name, tool in tools.items():
        row = expected[name]
        assert tool.get("outputSchema") == row["output_schema"] or (
            row["output_schema"] is None and "outputSchema" not in tool
        ), name
        if row["output_schema"]:
            declared += 1
            assert tool["outputSchema"]["type"] == "object"
        if row["array_schema"]:
            assert tool["_meta"] == {"ddflow/outputSchema": row["array_schema"]}, name
            assert "outputSchema" not in tool, "a root outputSchema must describe an object"
    assert declared > 40


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26"])
def test_a_revision_without_structured_output_is_served_as_before(on, version):
    server = _server(on, version)
    assert not any("outputSchema" in t or "_meta" in t for t in _tools(server).values())
    res = _rpc(server, "tools/call", {"name": "ddflow_status", "arguments": {}})
    assert "structuredContent" not in res


def _call(server: Server, name: str, **arguments) -> dict:
    return _rpc(server, "tools/call", {"name": name, "arguments": arguments})


@pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25"])
def test_an_object_result_is_also_structured_content(on, version):
    server = _server(on, version)
    res = _call(server, "ddflow_status")
    body = json.loads(res["content"][0]["text"])
    assert res["structuredContent"] == body
    assert body["schema"] == "status@1"
    tool = _tools(server)["ddflow_status"]
    assert body["schema"] == tool["outputSchema"]["properties"]["schema"]["const"]


def test_an_array_result_and_a_failed_call_carry_no_structured_content(on):
    server = _server(on, "2025-11-25")
    arr = _call(server, "ddflow_progress")
    assert isinstance(json.loads(arr["content"][0]["text"]), list)
    assert "structuredContent" not in arr
    bad = _call(server, "ddflow_gate_record", id="NO-SUCH-ITEM", gate="research", outcome="oops")
    assert bad["isError"] is True and "structuredContent" not in bad


def test_a_modern_request_is_structured_without_a_handshake(on):
    server = Server(on)
    tools = {t["name"]: t for t in _rpc(server, "tools/list", {"_meta": MODERN_META})["tools"]}
    assert "outputSchema" in tools["ddflow_status"]
    res = _rpc(
        server, "tools/call", {"name": "ddflow_status", "arguments": {}, "_meta": MODERN_META}
    )
    assert res["structuredContent"]["schema"] == "status@1"


def test_a_refusal_keeps_refusal_first_in_the_structured_object(on):
    server = _server(on, "2025-11-25")
    res = _call(server, "ddflow_claim", id="NO-SUCH-ITEM")
    assert res["_meta"]["exit"] == 3
    assert list(res["structuredContent"])[:2] == ["refusal", "schema"]


@pytest.mark.parametrize(("version", "structured"), [("2024-11-05", False), ("2025-11-25", True)])
def test_an_offloaded_worker_serves_the_revision_the_client_negotiated(on, version, structured):
    """The worker is a fresh process whose `Server` defaults to the newest revision; without
    the job's `protocol` an old client would get `structuredContent` on every long call."""
    import os
    import subprocess
    import sys

    job = {
        "repo": str(on),
        "called_from": str(on),
        "agent": "A",
        "protocol": version,
        "state_key": "",
        "msg": {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {}},
        },
    }
    root = str(Path(__file__).resolve().parents[1])
    done = subprocess.run(
        [sys.executable, "-c", "from ddflow.surfaces.mcp import _worker_main as m; m()"],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": root},
        timeout=120,
    )
    frames = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    result = frames[-1]["result"]
    assert ("structuredContent" in result) is structured, done.stderr[-400:]


def test_structured_content_is_json_as_sent_not_the_live_object():
    import datetime

    from ddflow.core import outcome as O
    from ddflow.surfaces.mcp import _outcome_result

    out = O.ok("x", when=datetime.date(2026, 10, 8), where=Path("/x"))
    res = _outcome_result(out, "", command="x", structured=True)
    assert res["structuredContent"] == {
        "schema": "x@1",
        "when": "2026-10-08",
        "where": "/x",
    }
    assert json.dumps(res)  # a plain writer can serialise the whole reply
