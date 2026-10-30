# Delta: Gemini CLI

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration.** ONE Gemini session runs to completion across its own multi-step loop:
   keep working task after task inside that session (each claim a wave of one, since
   below there is no fan-out) and stop only at an ask or when you have printed the
   handoff. Nothing persists between invocations, so across invocations the handoff is
   the printed `ddflow next` result and the current `ddflow cadence` output; a refresh
   of that print pastes straight back in and continues where it left off.
2. **Subagents.** No worktree-isolated subagent primitive. Run tasks sequentially, or
   have the operator start a second Gemini process — ddflow's leases coordinate
   *processes*, not threads, so two terminals are genuinely safe.
3. **Asking the operator.** Ask in plain text and stop.
4. **File references.** `@path` works; otherwise cat the file.
5. **Instructions file.** Gemini CLI reads `AGENTS.md`. Point it at the canonical driver
   there.

MCP registration in `.gemini/settings.json`:

```json
{ "mcpServers": { "ddflow": {
    "command": "python3", "args": ["-m", "ddflow", "--repo", ".", "mcp"] } } }
```

**Cross-family note:** when Gemini is the author, a Gemini reviewer does not satisfy
gate 4. Configure a non-Google reviewer, or record `unavailable` honestly.
