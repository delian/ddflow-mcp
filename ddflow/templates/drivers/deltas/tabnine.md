# Delta: Tabnine

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** One unit per request; print `ddflow next` and
   `ddflow cadence` as the handoff.
2. **Subagents.** None with worktree isolation. Use a second editor window or another
   agent in a terminal; ddflow's leases coordinate processes.
3. **Asking the operator.** Ask in the chat and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** Tabnine's own convention is
   `.tabnine/guidelines/*.md`, **not** `AGENTS.md`. `ddflow adopt` writes
   **`.tabnine/guidelines/ddflow.md`** with the managed block in it, so Tabnine reads the
   rules from the surface it actually consults. `ddflow doctor` reports drift between that
   copy and `AGENTS.md`. Your other guideline files are untouched.

## MCP registration

Written automatically into `.tabnine/agent/settings.json`, the **project** scope:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

The three scopes — project, user (`~/.tabnine/agent/settings.json`) and system — are
**shallow-merged**, not overridden, so a `mcpServers` map defined at a higher scope is
replaced wholesale by this one rather than merged key-by-key. If you rely on user-level
servers as well, declare them here too.
