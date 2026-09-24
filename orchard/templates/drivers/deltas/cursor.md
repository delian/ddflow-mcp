# Delta: Cursor

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** Cursor's Agent runs one request to completion; there
   is no persistent loop primitive. Do ONE task, then end your reply with the output of
   `orchard_next` and `orchard_cadence` — nothing persists those between requests, so
   printing them IS the handoff to the next request.
2. **Subagents.** Cursor has no worktree-isolated subagent. Two routes to parallelism,
   both safe because Orchard's leases coordinate *processes*:
   - open a second Cursor window on the same repo, or
   - run `cursor-agent` / another agent in a terminal on a different task.
   Each one claims its own item and gets its own worktree. Do not hand two tasks to one
   Cursor session and expect isolation — there is none.
3. **Asking the operator.** Ask in the chat and stop.
4. **File references.** `@file` / `@folder` in chat; plain paths in tool calls.
5. **Where the rules live.** Cursor's precedence is
   *Team Rules > Project Rules > User Rules > `.cursorrules` > `AGENTS.md`*, so
   `orchard adopt --agents cursor` writes **`.cursor/rules/orchard.mdc`** with
   `alwaysApply: true` as the binding copy, and `AGENTS.md` as the fallback. Both carry
   the same text from one source.
6. **Background Agents.** A Cursor Background Agent works in its own cloud checkout. Set
   `worktree.enabled = false` for those runs — the isolation a worktree would provide is
   already there, and creating one inside an ephemeral clone just adds a directory the
   merge step cannot reach. The lease still does its job: it stops a background agent
   and a local one taking the same item.

## MCP registration

Written automatically into `.cursor/mcp.json`:

```json
{ "mcpServers": { "orchard": { "command": "uvx", "args": ["orchard-mcp"] } } }
```

Cursor reads project MCP from `.cursor/mcp.json` and user-level MCP from
`~/.cursor/mcp.json`. The project file is the one to commit, so every contributor and
every Background Agent gets the same server without setup.

Enable the server in **Settings → MCP** the first time; Cursor requires explicit
approval for a newly-seen server, which is a per-machine step no config file can do for
you.
