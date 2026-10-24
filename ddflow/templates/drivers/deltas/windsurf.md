# Delta: Windsurf / Cascade

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Now Devin Desktop.** Windsurf was acquired by Cognition and its documentation now
   redirects to Devin Desktop. The `devin` target covers the Devin CLI, which does have a
   project file; this delta covers the editor.
2. **Iteration.** Cascade runs one request to completion: keep going inside it —
   claim, run the gates, merge — then re-read `ddflow next` for the next claim. At the
   end of each turn print `ddflow next` and `ddflow cadence`; that print is the handoff
   to the next request.
3. **Subagents.** None with worktree isolation. Open a second window or run another agent
   in a terminal; ddflow's leases coordinate processes.
4. **Asking the operator.** Ask in Cascade and stop.
5. **Where the rules live.** `AGENTS.md` (or `agents.md`, matched case-insensitively) is
   discovered automatically and fed into the same rules engine as `.devin/rules/` and the
   legacy `.windsurf/rules/` / `.windsurfrules`. So ddflow's `AGENTS.md` binds directly —
   no extra step. Note the rules engine's size caps: a file over them is truncated.

## MCP registration

**No project-level MCP file exists** — the current documentation describes a global config
only (`~/.config/devin/mcp_config.json`, or `%APPDATA%\devin\mcp_config.json` on
Windows). Add this there, or through the MCP panel:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

Several third-party pages claim a `~/.codeium/windsurf/mcp_config.json` or a project
`.devin/mcp_config.json` for the editor. Those contradict the official page and are not
what ddflow relies on.
