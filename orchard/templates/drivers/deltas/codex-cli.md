# Delta: OpenAI Codex CLI

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration.** One task per invocation unless run with an autonomous flag. End each
   invocation by printing the next `orchard next` result.
2. **Subagents.** None. Use parallel Codex processes; Orchard's leases make that safe.
3. **Asking the operator.** Plain text, then stop.
4. **File references.** Plain paths.
5. **Instructions file.** Codex reads `AGENTS.md` — point it at the canonical driver.
6. **Sandboxing.** Codex may run with restricted filesystem access. Worktrees are created
   *outside* the repo by default (`worktree.root`); if your sandbox forbids that, set
   `worktree.root = ".orchard-worktrees"` and add it to `.gitignore`.

MCP registration in `~/.codex/config.toml`:

```toml
[mcp_servers.orchard]
command = "python3"
args = ["-m", "orchard", "--repo", ".", "mcp"]
```

**Cross-family note:** a GPT reviewer does not satisfy gate 4 for a GPT author.
