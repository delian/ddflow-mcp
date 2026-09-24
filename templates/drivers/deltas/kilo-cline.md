# Delta: Kilo / Cline / Roo

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration is operator-driven.** No loop primitive. Run ONE task, then print the
   next `orchard next` and `orchard cadence` output — nothing persists between
   invocations, so printing them is the handoff.
2. **Subagents.** `task` tool, with worktree isolation where supported.
3. **Asking the operator.** The `question` tool.
4. **File references.** `@path`.
5. **Commands.** A slash command should inline BOTH files — the canonical driver and
   this delta — because a link is only followed if the agent chooses to follow it.

MCP registration in `.kilo/kilo.json`:

```json
{ "mcpServers": { "orchard": {
    "type": "local", "command": ["python3", "-m", "orchard", "--repo", ".", "mcp"],
    "timeout": 120 } } }
```
