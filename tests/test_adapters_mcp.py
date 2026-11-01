"""The MCP wire port: the same cases against the default adapter and its fake (ADP-mcp).

Written first: it fails until `ddflow.adapters.mcp` and `ddflow.adapters.fakes.mcp` exist.
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


def _fake(mode="ok"):
    server = FakeMcpServer(
        TOOLS, {"boom": W.ToolError("no")}, server_info={"name": "fake-mcp", "version": "9"}
    )
    return server, FakeMcpClient(server, mode=mode), lambda: server.sent


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


def test_tools_are_listed_and_called_and_a_tool_refusal_is_an_error_result(make):
    _, client, _ = make()
    client.connect(1)
    assert client.list_tools(1).result["tools"] == TOOLS
    got = client.call("echo", {"a": 1}, 1)
    assert got.status == W.OK and got.result["isError"] is False
    refused = client.call("boom", {}, 1)
    assert refused.status == W.OK and refused.result["isError"] is True
    unknown = client.call("nope", {}, 1)
    assert unknown.status == W.ERROR and unknown.error["code"] == W.INVALID_PARAMS


def test_calls_before_connect_are_unavailable_not_passed(make):
    _, client, _ = make()
    assert client.list_tools(1).status == W.UNAVAILABLE
    assert client.call("echo", {}, 1).answered is False


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


def test_the_server_answers_ping_unknown_methods_and_never_a_notification(make):
    server, _, _ = make()
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})["result"] == {}
    bad = server.handle({"jsonrpc": "2.0", "id": 2, "method": "nope/x"})
    assert bad["error"]["code"] == W.METHOD_NOT_FOUND and bad["id"] == 2
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    old = server.handle(W.initialize_request(3, "1999-01-01"))
    assert old["result"]["protocolVersion"] == W.SERVED_VERSIONS[0]


def test_serve_maps_a_bad_frame_and_an_escaped_exception_to_json_rpc_errors(make):
    server, _, sent = make()
    boom = lambda msg: 1 / 0  # noqa: E731
    server.serve(["not json\n", "[1]\n", "\n"])
    server.serve([W.encode(W.request(7, "ping")) + "\n"], boom)
    codes = [f["error"]["code"] for f in sent()]
    assert codes == [W.PARSE_ERROR, W.INVALID_PARAMS, W.INTERNAL_ERROR]
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
    assert taken == [1, 2, 3] and [f["id"] for f in sent()] == [1, 3] and len(after) == 2


def test_a_notification_is_written_whole():
    server, _, sent = _default()
    server.notify({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    assert sent()[0]["params"]["progress"] == 1


def test_the_default_adapter_imports_no_other_ddflow_layer_and_no_subprocess():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(W.__file__).read_text("utf-8"))
    mods = [
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [n])
    ]
    assert not [m for m in mods if m.startswith(("ddflow", ".")) or m == "subprocess"], mods
