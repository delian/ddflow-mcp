# Delta: OpenHands

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration.** OpenHands runs autonomous multi-step sessions. One claimed item at a
   time; print `ddflow next` at the end of each.
2. **Worktrees.** A hosted OpenHands runtime is already an isolated checkout — set
   `worktree.enabled = false` there. Locally, leave them on.
3. **Subagents.** Microagents are context, not parallel workers. Parallelism is a second
   OpenHands session on a different item, kept apart by the lease.
4. **Asking the operator.** Ask in the session and stop.
5. **Where the rules live.** `AGENTS.md` at the repo root, read automatically. Repository
   microagents live under `.openhands/microagents/` (or `.openhands/skills/`); a
   one-line microagent pointing at `AGENTS.md` is better than a second copy.

## MCP registration

**No project-level MCP file is written.** OpenHands' current documented path is the UI
— *Settings → MCP* — so that is where the server goes. A `config.toml` `[mcp]` section also exists:

```toml
[mcp]
stdio_servers = [
    {name="ddflow", command="uvx", args=["ddflow-mcp"]},
]
```

OpenHands' own docs label the stdio form development/testing only and not recommended for
production, and do not state a required repository path for `config.toml`. That is why it
is documented here rather than generated: ddflow does not write a config its vendor calls
unsupported.
