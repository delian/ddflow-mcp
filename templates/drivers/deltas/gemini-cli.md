# Delta: Gemini CLI

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration is operator-driven.** There is no persistent loop primitive. Run ONE task,
   then print: the next `orchard next` result, and the current `orchard cadence` output.
   Nothing persists those counters between invocations, so printing them IS the handoff.
2. **Subagents.** No worktree-isolated subagent primitive. Run tasks sequentially, or
   have the operator start a second Gemini process — Orchard's leases coordinate
   *processes*, not threads, so two terminals are genuinely safe.
3. **Asking the operator.** Ask in plain text and stop.
4. **File references.** `@path` works; otherwise cat the file.
5. **Instructions file.** Gemini CLI reads `AGENTS.md`. Point it at the canonical driver
   there.

MCP registration in `.gemini/settings.json`:

```json
{ "mcpServers": { "orchard": {
    "command": "python3", "args": ["-m", "orchard", "--repo", ".", "mcp"] } } }
```

**Cross-family note:** when Gemini is the author, a Gemini reviewer does not satisfy
gate 4. Configure a non-Google reviewer, or record `unavailable` honestly.
