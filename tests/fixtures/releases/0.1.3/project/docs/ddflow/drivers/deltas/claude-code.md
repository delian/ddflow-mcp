# Delta: Claude Code

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ. If you find yourself wanting to restate a rule from the
canonical driver here, don't — fix the canonical driver instead; every harness reads it.

1. **Iteration.** `/implement [phase or task id]` — written to `.claude/commands/` by
   `ddflow adopt` — runs `/loop` (self-paced) on the `implement` workflow command, which
   drives this driver until nothing actionable is left. `/mcp__ddflow__implement` is the
   same command without `/loop`: it drives as far as one turn allows, with no wake-up to
   carry it on. The continuation, each turn: a backgrounded
   command that *finishes* (e.g. `sleep 600`), then `ScheduleWakeup` re-passing the same
   `/loop` prompt. A timer alone is not a continuation mechanism; a completing background
   task re-invokes reliably. Kill the armed heartbeat before any deliberate stop —
   `ScheduleWakeup` with `stop: true` — or the stop is undone.
2. **Blocked behind another agent.** When `claim` is refused because another agent
   holds the item or overlapping files, or `next` has nothing ready while work is in
   flight, do not poll and do not ask the operator to tell you when to retry. Run
   `ddflow wait --item <id>` (or `ddflow wait` for "anything") with Bash
   `run_in_background: true`: it sleeps on the event log and exits the moment the
   holder completes, releases or lets its lease expire, and the harness re-invokes you
   when it exits. Exit 0 means claim now; exit 2 means the deadline passed or waiting
   cannot help, and its message says which and what to do instead. Take other ready
   work meanwhile if there is any.
3. **Subagents.** `Agent` tool with `isolation: "worktree"`. Pass `model:` explicitly on
   every dispatch — a reviewer must never inherit the author's model. Route cheap sweeps
   to a small model, reviews to a *different family*, and the merge decision to a
   frontier model.
4. **Asking the operator.** `AskUserQuestion`.
5. **File references.** `@path`.
6. **Rules files.** Keep `CLAUDE.md` pointing at the canonical driver rather than
   restating it. `ddflow brief` supplies the per-task lesson retrieval that
   `CLAUDE.md` would otherwise have to mandate by prose.

Register the MCP server in `.mcp.json`:

```json
{ "mcpServers": { "ddflow": {
    "command": "python3", "args": ["-m", "ddflow", "--repo", ".", "mcp"],
    "env": { "PYTHONPATH": "vendor/ddflow" } } } }
```
