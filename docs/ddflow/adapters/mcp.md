# Adapter family `mcp`

Port: **McpServer, McpClient**, declared in `ddflow/adapters/mcp.py` and marked `@port`.

The MCP wire: JSON-RPC framing for the server side (a declared tool table) and the client side (start a server through an injected `Starter`, `initialize`, list and call tools with timeout and unavailable semantics). Two ports in one module because they share the framing.

- **Default adapter:** `ddflow.adapters.mcp` (`StdioServer`, `StdioClient`); no subprocess here, the caller injects the starter.
- **Fake:** `FakeMcpServer` and `FakeMcpClient` in `ddflow/adapters/fakes/mcp.py`: in memory; the client takes `mode=` `silent`, `gone`, `echo` or `unavailable`.
- **Conformance cases:** tests/test_adapter_mcp.py.

The pattern is in [README.md](README.md).
