# Delta: Qodo

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Two products.** **Qodo Command** (the CLI) reads a project-root `mcp.json`, which is
   what ddflow writes. The **Qodo Gen** IDE plugin keeps per-user MCP config in the IDE,
   and organisations have an `org_mcp.json` — neither is a repo file.
2. **Iteration is operator-driven.** One unit per invocation; print `ddflow next` and
   `ddflow cadence` as the handoff.
3. **Subagents.** Qodo agents are declared in `.toml` files that list `available_tools`;
   reference the `ddflow` server there so an agent can actually call it.
4. **Asking the operator.** Ask and stop.
5. **Where the rules live.** `AGENTS.md` — Qodo detects and reads it automatically.
   `best_practices.md` at the project root additionally drives its review behaviour.

## MCP registration

Written automatically into `mcp.json` at the project root:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

**Note the unqualified filename.** If your project already uses `mcp.json` at the root
for something else, point Qodo at a different path in its agent `.toml` instead of
letting two tools fight over one name.

For **Qodo Gen** in the IDE, add the same entry through *Add new MCP* — a per-user step
no adopter can do for you.
