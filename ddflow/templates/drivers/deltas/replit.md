# Delta: Replit Agent

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration.** Replit Agent runs long autonomous sessions. ddflow's unit is still one
   claimed item; print `ddflow next` at the end of each.
2. **Worktrees off.** A Replit workspace is already an isolated environment, and git
   worktrees inside it add a directory the merge step cannot reach. Set
   `worktree.enabled = false`.
3. **Subagents.** None. The lease is what stops two sessions taking one item.
4. **Asking the operator.** Ask in the chat and stop.
5. **Where the rules live.** **`replit.md`** at the project root — Replit's own
   convention, auto-detected, and it must be at the root. `ddflow adopt` writes the managed
   block into `replit.md` as well as `AGENTS.md`, preserving anything already in it, and
   `ddflow doctor` reports either copy drifting.

## MCP registration

**No repo-level MCP file exists.** Replit configures MCP entirely in the web UI
(*Integrations → MCP Servers for Replit Agent*), and its documented model is **remote
servers added by URL**, not local stdio processes. ddflow's server is stdio, so on Replit
the practical route is the **CLI**: `ddflow brief`, `ddflow next`, `ddflow claim`, which
needs no MCP client at all.
