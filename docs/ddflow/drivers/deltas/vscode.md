<!-- ddflow:begin drivers/deltas/vscode ddflow=0.2.0 fmt=1 sha=19a967e4d961 -->
# Delta: VS Code (any agent)

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Scope.** This covers VS Code's OWN MCP support, which every VS Code agent shares —
   Copilot Chat, and any extension that speaks MCP. GitHub Copilot also has surfaces
   outside VS Code (a CLI, a cloud coding agent); those are `github-copilot.md`. Adopt
   **both** if you use Copilot inside VS Code and on the command line.
2. **Iteration is operator-driven.** Agent mode runs one request to completion. Do ONE
   task, then end your reply with `ddflow_next` and `ddflow_cadence` output — nothing
   persists those between requests, so printing them IS the handoff.
3. **Subagents.** None with worktree isolation. For parallelism open a second window on
   the same repo, or run another agent in a terminal; ddflow's leases coordinate
   *processes*, so each claims its own item and gets its own worktree.
4. **Asking the operator.** Ask in chat and stop.
5. **Where the rules live.** `AGENTS.md`. VS Code has no rules format of its own.

## MCP registration

Written automatically into `.vscode/mcp.json`. Note the top-level key is **`servers`**,
not `mcpServers`, and the transport is named:

```json
{ "servers": { "ddflow": { "type": "stdio", "command": "uvx", "args": ["ddflow-mcp"] } } }
```

VS Code also accepts a root `.mcp.json` using `mcpServers` as a portable format shared
with Claude Code and Cursor. ddflow writes the native `.vscode/mcp.json`; if you would
rather have the portable one, adopt `claude` as well — it writes exactly that file.
<!-- ddflow:end drivers/deltas/vscode -->
