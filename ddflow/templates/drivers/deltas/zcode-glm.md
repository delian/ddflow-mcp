# Delta: ZCode (GLM / Zhipu)

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** One request to completion; print `ddflow next` and
   `ddflow cadence` as the handoff.
2. **Subagents.** Where supported, one claimed item each.
3. **Asking the operator.** Ask and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md`. ZCode's own instruction-file convention is not
   documented publicly at the time of writing, so `AGENTS.md` is what ddflow writes.

## MCP registration

Written automatically into `.zcode/config.json`. Servers nest under **`mcp` → `servers`**,
which is ZCode's native shape:

```json
{ "mcp": { "servers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } } }
```

ZCode also accepts the industry-standard `.agents/mcp.json` with a top-level
`mcpServers`. The native file is the one that binds, so that is the one written.
