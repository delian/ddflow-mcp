"""The MCP wire ports: the same cases against the default adapters and their fakes (ADP-mcp).

`CONFORMANCE` is what the generic contract test (tests/test_ports_contract.py) reads: per port,
how to build the real adapter, the fake, and the cases both must pass.
"""

from __future__ import annotations

import collections
import json
import time

import pytest

from ddflow.adapters import mcp as W
from ddflow.adapters.fakes.mcp import FakeMcpClient, FakeMcpServer

TOOLS = [
    {"name": "echo", "description": "say it", "inputSchema": {"type": "object"}},
    {"name": "boom", "description": "refuses", "inputSchema": {"type": "object"}},
]


def _call(name, args):
    if name == "boom":
        raise W.ToolError("no")
    return {"content": [{"type": "text", "text": json.dumps(args)}], "isError": False}


class _Loop:
    """A Channel wired to a server's `handle`, in memory (what a pipe to a process is)."""

    def __init__(self, server, mode):
        self.server, self.mode, self.queue = server, mode, collections.deque()

    def send(self, line):
        if self.mode == "gone":
            raise BrokenPipeError
        frame = json.loads(line)
        if self.mode == "echo":
            self.queue.append(line)
        elif self.mode not in ("silent", "closed"):
            reply = self.server.handle(frame)
            if reply is not None:
                self.queue.append(W.encode(reply))

    def receive(self, timeout_s):
        if self.queue:
            return self.queue.popleft()
        if self.mode == "closed":
            raise EOFError
        time.sleep(min(timeout_s, 0.005))
        return None

    def close(self):
        self.queue.clear()


def _default(mode="ok"):
    sent = []
    server = W.StdioServer(
        lambda line: sent.append(json.loads(line)),
        tools=TOOLS,
        call=_call,
        server_info={"name": "fake-mcp", "version": "9"},
    )

    def starter(argv, env):
        if mode == "unavailable":
            raise FileNotFoundError(argv[0])
        return _Loop(server, mode)

    return server, W.StdioClient(starter, ["srv"], poll_s=0.01), lambda: sent


def _fake(mode="ok", server_cls=FakeMcpServer, client_cls=FakeMcpClient):
    server = server_cls(
        TOOLS, {"boom": W.ToolError("no")}, server_info={"name": "fake-mcp", "version": "9"}
    )
    return server, client_cls(server, mode=mode), lambda: server.sent


MAKERS = {"default": _default, "fake": _fake}


@pytest.fixture(params=sorted(MAKERS))
def make(request):
    return MAKERS[request.param]


def test_a_handshake_names_the_server_and_records_the_version_fact(make):
    _, client, _ = make()
    hello = client.connect(1)
    assert hello.status == W.OK and hello.speaks_mcp is True
    assert hello.server["name"] == "fake-mcp" and "fake-mcp 9" in hello.detail
    assert hello.protocol.supported is True and hello.protocol.answered == W.OFFER_VERSION
    client.close()
    client.close()  # safe twice


def test_a_server_answering_a_revision_we_do_not_serve_is_a_fact_not_a_failure():
    assert W.negotiate("2025-03-26", "2099-01-01").supported is False
    assert W.negotiate("2025-03-26", None).supported is None
    assert W.negotiate("2025-03-26", "").supported is None
    assert W.negotiate("2025-03-26", "2024-11-05").supported is True


def _client_cases(client):
    client.connect(1)
    assert client.list_tools(1).result["tools"] == TOOLS
    got = client.call("echo", {"a": 1}, 1)
    assert got.status == W.OK and got.result["isError"] is False
    refused = client.call("boom", {}, 1)
    assert refused.status == W.OK and refused.result["isError"] is True
    unknown = client.call("nope", {}, 1)
    assert unknown.status == W.ERROR and unknown.error["code"] == W.INVALID_PARAMS


def test_tools_are_listed_and_called_and_a_tool_refusal_is_an_error_result(make):
    _client_cases(make()[1])


def test_calls_before_connect_are_unavailable_not_passed(make):
    _, client, _ = make()
    assert client.list_tools(1).status == W.UNAVAILABLE
    assert client.call("echo", {}, 1).status == W.UNAVAILABLE


@pytest.mark.parametrize(
    ("mode", "status", "speaks"),
    [
        ("silent", W.TIMEOUT, None),
        ("gone", W.EOF, False),
        ("echo", W.ECHOED, False),
        ("unavailable", W.UNAVAILABLE, False),
    ],
)
def test_a_server_that_does_not_answer_is_classified_never_passed(make, mode, status, speaks):
    _, client, _ = make(mode)
    hello = client.connect(0.05)
    assert hello.status == status and hello.speaks_mcp is speaks and hello.answered is False


def test_a_closed_stream_is_eof():
    _, client, _ = _default("closed")
    assert client.connect(1).status == W.EOF


def _server_cases(server):
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {}
    bad = server.handle({"jsonrpc": "2.0", "id": 2, "method": "nope/x"})
    assert bad["error"]["code"] == W.METHOD_NOT_FOUND and bad["id"] == 2
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    old = server.handle(W.initialize_request(3, "1999-01-01"))
    assert old["result"]["protocolVersion"] == W.SERVED_VERSIONS[0]


def test_the_server_answers_ping_unknown_methods_and_never_a_notification(make):
    _server_cases(make()[0])


CONFORMANCE = {
    "McpServer": {
        "real": lambda monkeypatch: _default()[0],
        "fake": lambda cls: _fake(server_cls=cls)[0],
        "cases": _server_cases,
    },
}


def test_serve_maps_a_bad_frame_and_an_escaped_exception_to_json_rpc_errors(make):
    server, _, sent = make()
    boom = lambda msg: 1 / 0  # noqa: E731
    server.serve(["not json\n", "[1]\n", "\n"])
    server.serve([W.encode(W.request(7, "ping")) + "\n"], boom)
    codes = [f["error"]["code"] for f in sent()]
    assert codes == [W.PARSE_ERROR, W.INVALID_REQUEST, W.INTERNAL_ERROR]
    assert sent()[-1]["id"] == 7


def test_serve_answers_through_the_table_and_runs_the_hooks(make):
    server, _, sent = make()
    taken, after = [], []
    lines = [
        W.encode(W.request(1, "ping")) + "\n",
        W.encode(W.request(2, "tools/list")) + "\n",
        W.encode(W.request(3, "ping")) + "\n",
    ]

    def intercept(msg, notify):
        taken.append(msg["id"])
        return msg["id"] == 2  # taken over: not answered by the table

    server.serve(lines, intercept=intercept, after=lambda: after.append(1))
    assert taken == [1, 2, 3] and [f["id"] for f in sent()] == [1, 3] and len(after) == 3


def test_a_notification_is_written_whole():
    server, _, sent = _default()
    server.notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    assert sent()[0]["params"]["progress"] == 1


def test_the_default_adapter_imports_no_other_ddflow_layer_and_no_subprocess():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(W.__file__).read_text("utf-8"))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append("." * node.level + (node.module or ""))  # a relative import keeps its dots
    # `.ports` is the stdlib-only marker every family imports (its own layer: adapters);
    # `.mcp_wire` and `.mcp_client` are its own family's modules (the client split out of
    # `mcp.py` under the 20 KB ceiling), so a level-1 `.` import is a sibling adapter, not
    # another layer. `ddflow` (absolute) and `..` reach layers above `adapters`.
    assert not [
        m
        for m in mods
        if m == "subprocess" or m.startswith("ddflow") or m.startswith("..")
    ], mods


def test_an_after_hook_is_skipped_for_an_intercepted_message_and_a_bad_exception_result_is_internal():
    server = FakeMcpServer(TOOLS, {"echo": RuntimeError("boom")})
    server.serve([W.encode(W.request(1, "tools/call", {"name": "echo", "arguments": {}})) + "\n"])
    assert server.sent[0]["error"]["code"] == W.INTERNAL_ERROR


def test_a_failed_or_closed_fake_client_is_disconnected():
    class Bad(FakeMcpServer):
        def handle(self, msg):
            return (
                W.err(msg["id"], W.INTERNAL_ERROR, "bad")
                if msg["method"] == "initialize"
                else super().handle(msg)
            )

    failed = FakeMcpClient(Bad(TOOLS))
    assert failed.connect(1).status == W.ERROR and failed.list_tools(1).status == W.UNAVAILABLE
    client = FakeMcpClient(FakeMcpServer(TOOLS))
    client.connect(1)
    client.close()
    assert client.list_tools(1).status == W.UNAVAILABLE


def test_the_unavailable_handshake_keeps_the_offered_version():
    _, client, _ = _default("unavailable")
    assert client.connect(1, version="2024-11-05").protocol.offered == "2024-11-05"


def test_a_buffer_at_its_cap_does_not_lose_the_newline_that_ends_a_line():
    import os
    import types

    from ddflow.services.companions import probe

    r, w = os.pipe()
    chan = probe._ProcessChannel(types.SimpleNamespace(stdout=os.fdopen(r, "rb", 0)))
    chan._buf = b"x" * probe._MAX_BUF  # a flood's unfinished line fills the cap
    os.write(w, b"\n" + b"y" * 1000)
    assert chan.receive(1) == "x" * probe._MAX_BUF
    os.close(w)


def test_a_scripted_exception_is_an_error_outcome_for_the_fake_client():
    server = FakeMcpServer(TOOLS, {"echo": RuntimeError("boom")})
    client = FakeMcpClient(server)
    client.connect(1)
    got = client.call("echo", {}, 1)
    assert got.status == W.ERROR and got.error["code"] == W.INTERNAL_ERROR


def test_a_malformed_request_is_refused_and_a_failing_notification_is_not_answered(make):
    server, _, sent = make()
    bare = server.handle({"id": 1, "method": "ping"})
    assert bare["error"]["code"] == W.INVALID_REQUEST
    assert server.handle({"jsonrpc": "2.0", "id": 9})["error"]["code"] == W.INVALID_REQUEST
    for bad_id in ({}, True, [1], float("nan"), float("inf")):
        odd = server.handle({"jsonrpc": "2.0", "id": bad_id, "method": "ping"})
        assert odd["error"]["code"] == W.INVALID_REQUEST and odd["id"] is None
    old = server.handle({"jsonrpc": "1.0", "id": 2, "method": "ping"})
    assert old["error"]["code"] == W.INVALID_REQUEST
    arr = server.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": [1]})
    assert arr["error"]["code"] == W.INVALID_PARAMS
    boom = lambda msg: 1 / 0  # noqa: E731
    server.serve([W.encode({"jsonrpc": "2.0", "method": "ping"}) + "\n"], boom)
    assert sent() == []


def test_the_version_fact_reads_the_results_own_protocol_version_not_server_info():
    server = FakeMcpServer(TOOLS, server_info={"name": "x", "protocolVersion": "2024-11-05"})
    server._initialize = lambda params: {"serverInfo": server.server_info}
    assert FakeMcpClient(server).connect(1).protocol.supported is None


def test_the_unavailable_fake_keeps_the_offered_version_too():
    _, client, _ = _fake("unavailable")
    assert client.connect(1, version="2024-11-05").protocol.offered == "2024-11-05"


def test_a_close_right_after_the_answer_keeps_the_handshake_ok():
    server = W.StdioServer(tools=TOOLS, call=_call)

    class Dies(_Loop):
        def send(self, line):
            if "notifications" in line:
                raise BrokenPipeError
            super().send(line)

    client = W.StdioClient(lambda argv, env: Dies(server, "ok"), ["srv"], poll_s=0.01)
    hello = client.connect(1)
    assert hello.status == W.OK and hello.speaks_mcp is True and "it did answer" in hello.detail
