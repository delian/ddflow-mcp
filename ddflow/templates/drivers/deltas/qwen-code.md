# Delta: Qwen Code CLI

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** One request to completion; print `ddflow next` and
   `ddflow cadence` as the handoff.
2. **Subagents.** As Gemini CLI — one claimed item each.
3. **Asking the operator.** Ask and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** Qwen Code's default context file is **`QWEN.md`**, and it
   reads `AGENTS.md` when present. `ddflow adopt` writes the managed block into BOTH — the
   rules themselves, not a pointer, because a link is only followed if the agent chooses to
   follow it. `ddflow doctor` compares every copy against one generator and reports drift,
   so the duplication is owned by a check rather than by you.

## MCP registration

Written automatically into `.qwen/settings.json` (Qwen Code is a Gemini CLI fork, so the
shape matches `.gemini/settings.json`):

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

Per-server `timeout` and `trust` keys are also supported; `trust: true` skips the
confirmation prompt for every call, which is a decision about this project, not a default
ddflow should make for you.
