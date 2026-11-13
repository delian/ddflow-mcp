# Delta: Mistral Vibe

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration.** One request runs to completion of one task. Within the request, keep
   going through claim, the gates and the merge; the request ends when the merge lands or
   an ask stops you.
2. **Subagents.** Vibe spawns subagents through its built-in task tool; custom subagents
   are `agents/*.toml` files.
3. **Asking the operator.** Ask and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md`. Vibe reads `AGENTS.md` from the user home and
   the project directories up to the trust root; ddflow writes no separate rules surface.

## MCP registration

Written into `.vibe/config.toml` under `[[mcp_servers]]` (the `toml.mcp_servers` shape).
Vibe reads the project file after the trust step (`trusted_folders.toml`), so a lane home
pre-trusts the worktree. Register with `vibe mcp add` or write the entry directly:

```toml
[[mcp_servers]]
name = "ddflow"
command = "uvx"
args = ["ddflow-mcp"]
```
