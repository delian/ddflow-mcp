# Delta: opencode

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** One request to completion. Print `ddflow next` and
   `ddflow cadence` at the end of each turn.
2. **Subagents.** opencode supports agents/modes; each should claim its own item.
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
