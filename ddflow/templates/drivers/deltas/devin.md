# Delta: Devin (CLI and cloud)

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Two products, one of which no repo file can configure.** The **Devin CLI** reads
   `.devin/mcp_config.json` from the project, and that is what ddflow writes. **Cloud
   Devin sessions** are configured in the web UI / org settings instead.
2. **Iteration.** Cloud sessions run long and autonomously; the CLI runs per invocation.
   Either way, one claimed item at a time, and print `ddflow next` at the end.
3. **Subagents.** A cloud session already works in its own checkout, so set
   `worktree.enabled = false` for those runs — the isolation is already there, and a
   worktree inside an ephemeral clone is a directory the merge step cannot reach.
4. **Asking the operator.** Cloud: the session UI. CLI: ask and stop.
5. **Where the rules live.** `AGENTS.md` at the repo root, read automatically.

## MCP registration

Written automatically into `.devin/mcp_config.json` — the git-tracked **project** scope:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

A `.devin/mcp_config.local.json` sibling exists for secrets and is gitignored; ddflow
writes neither secrets nor that file. **Whether this project file also applies to cloud
Devin sessions is not documented** — the docs describe it under the CLI — so treat the
cloud side as the manual step and add the server in its settings too.
