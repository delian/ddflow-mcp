"""The MCP client port: the same cases against the default adapter and its fake (ADP-mcp).

`CONFORMANCE` is what the generic contract test (tests/test_ports_contract.py) reads: per
port, how to build the real adapter, the fake, and the cases both must pass. The client
split from `mcp.py` into its own module (`mcp_client.py`, the one home a protocol revision
touches), so its conformance is its own per-family file; the server sibling and the wire
scaffolding stay in `test_adapter_mcp.py`.
"""

from __future__ import annotations

from test_adapter_mcp import _client_cases, _default, _fake

CONFORMANCE = {
    "McpClient": {
        "real": lambda monkeypatch: _default()[1],
        "fake": lambda cls: _fake(client_cls=cls)[1],
        "cases": _client_cases,
    },
}
