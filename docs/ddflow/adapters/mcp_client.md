# Adapter family `mcp_client`

Port: **McpClient**, declared in `ddflow/adapters/mcp_client.py` and marked `@port`.

The MCP **client** side: start a server through an injected `Starter`, `initialize`, `list_tools`, `call`, with timeout and unavailable semantics. It holds no `subprocess` (the caller injects the starter), and no wire framing of its own — the JSON-RPC framing stays in `ddflow.adapters.mcp_wire`, the one home a protocol revision or an SDK swap touches. Split out of `mcp` so neither server nor client alone outgrows the 20 KB adapter ceiling (`mcp` re-exports every name here).

- **Default adapter:** `McpClient`'s `StdioClient`, over an injected `Starter`; silence is `timeout` (unknown), never a pass, and a server that cannot start is `unavailable`.
- **Fake:** `FakeMcpClient` in `ddflow/adapters/fakes/mcp.py`: scripts the failure a real server can show (`silent`, `gone`, `echo`, `unavailable`).
- **Conformance cases:** tests/test_adapter_mcp_client.py.

The pattern is in [README.md](README.md).
