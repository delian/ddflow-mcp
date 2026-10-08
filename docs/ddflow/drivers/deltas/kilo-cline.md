<!-- ddflow:begin drivers/deltas/kilo-cline ddflow=0.2.0 fmt=1 sha=3b1109e4343a -->
# Delta: Kilo / Cline / Roo

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration is operator-driven.** No loop primitive. Run ONE task, then print the
   next `ddflow next` and `ddflow cadence` output — nothing persists between
   invocations, so printing them is the handoff.
2. **Subagents.** `task` tool, with worktree isolation where supported.
3. **Asking the operator.** The `question` tool.
4. **File references.** `@path`.
5. **Commands.** A slash command should inline BOTH files — the canonical driver and
   this delta — because a link is only followed if the agent chooses to follow it.

MCP registration in `.kilo/kilo.json`:

```json
{ "mcp": { "ddflow": {
    "type": "local", "command": ["python3", "-m", "ddflow", "--repo", ".", "mcp"],
    "enabled": true } } }
```

The key is `mcp`, not `mcpServers`: Kilo silently ignores an `mcpServers` block. `command`
is ONE array including the arguments. `timeout`, if you add one, is in MILLISECONDS
(default 10000). Source: https://kilo.ai/docs/automate/mcp/using-in-cli
<!-- ddflow:end drivers/deltas/kilo-cline -->
