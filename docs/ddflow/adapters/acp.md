# Adapter family `acp`

Ports: **AcpAgent** (serve ddflow as an ACP agent) and **AcpClient** (drive a lane over ACP), both declared in `ddflow/adapters/acp.py` and marked `@port`.

ACP is JSON-RPC over stdio. The agent half answers `initialize`, `session/new` (the client's injected MCP servers are kept on the session), `session/prompt` (streaming `session/update`) and `session/cancel`. The client half negotiates the version (a three-valued fact: served, another number, or unknown for silence), opens a session, prompts it, maps updates to the common lane events (`message`, `thought`, `tool`, `tool_update`, `plan`, `other`) and answers permission requests through the caller's policy; with no policy it refuses, it never allows. A missing agent is `unavailable`, silence is `timeout`, never a pass.

- **Default adapters:** `AcpStdioAgent` and `AcpStdioClient` in `ddflow.adapters.acp`. Framing, `Channel`, `Starter` and `Outcome` are the MCP wire's (`ddflow.adapters.mcp`). The client takes an injected `Starter` wired from `ddflow.infra.proc`; the adapter spawns nothing.
- **Fakes:** `FakeAcpAgent` replays a `Script` (updates, permission asks, stop reason); `FakeAcpClient` drives it in memory, with `mode` = `silent`, `gone`, `closed`, `unavailable`.
- **Conformance cases:** tests/test_adapter_acp.py.
- **Adopters:** DSH-lane-acp and VB-acp take the port when they land.

The pattern is in [README.md](README.md).
