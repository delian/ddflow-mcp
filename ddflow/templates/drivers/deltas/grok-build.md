# Delta: Grok Build

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full. Only the
items below differ. Grok Build is xAI's terminal agent (binary `grok`), not the community
`grok-cli`. Facts marked (3p) are third party and not verified against the official docs.

1. **Iteration.** A session runs to completion; headless is `grok -p "<prompt>"`. Nothing
   persists between runs, so end with the printed `ddflow next` result and `ddflow cadence`
   output as the handoff. `-r <ID>` resumes a session.
2. **Subagents.** Grok runs subagents as child sessions and reads definitions from
   `.grok/agents/`. Worktree isolation is not documented (`grok -w` is 3p): give each task
   its own ddflow claim, whose lease coordinates processes.
3. **Asking the operator.** Ask in plain text and stop.
4. **Instructions.** Grok reads `AGENTS.md` (also `Agents.md`, `AGENT.md`) from the current
   directory up to the repo root, and Claude Code's instruction files, so `CLAUDE.md` works.
5. **MCP.** `.grok/config.toml`, a `[mcp_servers.ddflow]` table (`grok mcp add --scope
   project` writes it). Project MCP servers need the folder trusted.
6. **Hooks.** ddflow writes `.grok/hooks/ddflow.json`. Project hooks do not run until the
   operator grants trust with `/hooks-trust` (or `grok --trust`): tell the operator. Only
   PreToolUse is described as blocking; every failure is fail-open. Injection by hook is
   unverified (3p: PreToolUse and PostToolUse only, SessionStart discarded), so context
   reaches the model through the first MCP call.
7. **Probe to run with consent:** `grok --version` and `grok inspect` (lists config
   sources, instructions, skills, plugins, hooks, MCP servers).

**Cross-family note:** when Grok is the author, a Grok (xAI) reviewer does not satisfy the
independence gate. Use another family, or record `unavailable`.
