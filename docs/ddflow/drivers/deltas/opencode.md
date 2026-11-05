<!-- ddflow:begin drivers/deltas/opencode ddflow=0.2.10 fmt=1 sha=ba976f05c01d -->
# Delta: opencode

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration runs in the background; the operator is the exception, not the driver.**
   A background sub-agent runs the wave loop off the operator's chat: one read of
   `ddflow next --json`, claim each ready item, then fan the claimed items to sub-agents
   (opencode v1 config agents in `opencode.json` `$`-agents; zen agents at
   `~/.config/opencode/agent/*.md`) — one item each, mapping `tier:fast|balanced|deep`
   to model choice — and after each merge or park re-read `next` for the next wave. Nothing
   persists between requests, so across requests the handoff is the printed `ddflow next` +
   `ddflow cadence` at the turn's end; the wave position is ddflow's own committed event
   log. Stop only on an operator answer (an approval, a policy decision) or when the scope
   is empty. These loop facts are the `[orchestration]` record in
   `ddflow/harnesses/opencode.toml`.
2. **Subagents.** opencode supports agents/modes, each its own background item — never a
   foreground loop in the operator's chat.
3. **Asking the operator.** Ask and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md`.

## MCP registration

Written automatically into `opencode.json` at the project root. **opencode's shape is its
own** — `command` is ONE array that includes the arguments, the transport is named, and
`enabled` is explicit:

```json
{ "mcp": { "ddflow": {
    "type": "local", "command": ["uvx", "ddflow-mcp"], "enabled": true } } }
```

Handing opencode the `{"command": "uvx", "args": [...]}` form used by most other agents
produces valid JSON that starts nothing, which is why this file is generated rather than
copied from another agent's.
<!-- ddflow:end drivers/deltas/opencode -->
