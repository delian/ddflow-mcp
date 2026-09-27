# Delta: Kimi Code CLI

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** Run ONE task, then print `ddflow next` and
   `ddflow cadence` — that output is the handoff to the next invocation.
2. **Subagents.** Kimi Code has sub-agents; give each its own claimed item, never two
   tasks to one session expecting isolation.
3. **Asking the operator.** Ask in the session and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md`.

## MCP registration

Written automatically into `.kimi-code/mcp.json`, the PROJECT-level file, which takes
precedence over the user-level `~/.kimi-code/mcp-config`:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

An entry with a `command` field is a stdio server. Note this is **not** a repo-root
`.mcp.json`: several third-party write-ups say Kimi reuses Claude Code's file, and the
official documentation says the config is `.kimi-code/mcp.json`. Kimi also offers
`/mcp-config` to add servers conversationally, which edits the same file.
