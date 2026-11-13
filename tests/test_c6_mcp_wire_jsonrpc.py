"""C6-mcp-wire-jsonrpc: the MCP wire module is the home of the hand-written JSON-RPC framing,
so its sites are not violations; the baselined worker site is still counted."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import archifaces as I

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_wire_is_the_jsonrpc_home_and_nothing_else_is_added() -> None:
    now = I.counts(I.scan(ROOT))
    assert not [k for k in now if k[:2] == ("protocol", "jsonrpc") and k[2].endswith("mcp_wire.py")]
    _task, homes = I.home_of("protocol", "jsonrpc")
    assert homes == ("ddflow.adapters.mcp_wire",)
    assert now.get(("protocol", "jsonrpc", "ddflow/surfaces/worker.py")) == 1
