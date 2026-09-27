# Delta: GitHub Copilot (CLI and coding agent)

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Three surfaces, two of them outside VS Code.** The VS Code extension reads
   `.vscode/mcp.json` — that is the `vscode` target, adopt it too. This delta covers the
   **Copilot CLI** (the `copilot` command) and the **cloud coding agent**.
2. **Iteration.** The CLI runs one request to completion; the cloud agent runs to a PR.
   Neither has a loop primitive, so print `ddflow next` and `ddflow cadence` at the end of
   each turn as the handoff.
3. **Subagents.** None with worktree isolation. The cloud coding agent already works in
   its own checkout, so set `worktree.enabled = false` for those runs: the isolation is
   already there, and a worktree inside an ephemeral clone is a directory the merge step
   cannot reach. The lease still stops the cloud agent and a local one taking one item.
4. **Asking the operator.** CLI: ask and stop. Cloud agent: leave it in the PR.
5. **Where the rules live.** `AGENTS.md`, or `.github/copilot-instructions.md`. The CLI
   also reads `CLAUDE.md` and follows `@relative/path` includes.

## MCP registration

Written automatically into `.github/mcp.json` — the committed, shared file, rather than
the `.mcp.json` the CLI also searches for upward from the working directory:

```json
{ "mcpServers": { "ddflow": {
    "type": "local", "command": "uvx", "args": ["ddflow-mcp"], "tools": ["*"] } } }
```

`type: "local"` is what Copilot calls a stdio server, and **`tools` is an allowlist** —
without it a server's tools are registered and never offered. `["*"]` means all of
ddflow's.

**The cloud coding agent is configured in the repository's own settings**
(*Settings → Copilot → Coding agent → MCP configuration*), not by any file in the repo.
That is a one-time manual step no adopter can perform for you; paste the same JSON there.
