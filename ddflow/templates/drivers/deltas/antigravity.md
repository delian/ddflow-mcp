# Delta: Google Antigravity

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration.** Antigravity is agent-first and can run multi-step work, but ddflow's
   loop is still one task per claim. Print `ddflow next` and `ddflow cadence` at the end
   of each unit.
2. **Subagents.** Where the IDE runs several agents, each must claim its own item — the
   lease is what keeps two of them off one file.
3. **Asking the operator.** Ask in the agent surface and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md` or `GEMINI.md` at the repo root, or
   `.agents/rules/` for workspace rules. ddflow writes `AGENTS.md`.

## MCP registration

Written automatically into `.agents/mcp_config.json`, the workspace-local config
(the global one is `~/.gemini/config/mcp_config.json`):

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

Documented per-server keys are `command`, `args`, `env` and `cwd`. Remote transports need
`serverUrl` — the legacy `url` / `httpUrl` keys are not supported.
