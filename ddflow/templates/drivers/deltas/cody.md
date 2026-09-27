# Delta: Sourcegraph Cody

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Check availability first.** Cody Free and Pro were discontinued on 2025-07-23;
   **Cody Enterprise remains supported**. If you are not on Enterprise, this delta does
   not apply to you.
2. **Iteration is operator-driven.** One unit per request; print `ddflow next` and
   `ddflow cadence` as the handoff.
3. **Subagents.** None with worktree isolation. Use a second editor window or another
   agent in a terminal; the lease keeps them apart.
4. **Asking the operator.** Ask in the chat and stop.
5. **Where the rules live.** Whether Cody reads `AGENTS.md` is **not documented either
   way** in its current docs, so do not assume it does. Treat the shell surface as
   primary here: `ddflow brief`, `ddflow next`, `ddflow claim`.

## MCP registration

**No project-level file exists.** Cody is configured through the editor's own settings —
VS Code `settings.json` or JetBrains `cody_settings.json` — under a `cody.mcpServers` key
nested inside that file, not a standalone config:

```json
"cody.mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } }
```

Because that is editor/user configuration rather than a repo file, it is a per-machine
step for each contributor.
