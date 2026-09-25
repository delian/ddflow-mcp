# Delta: Claude Code

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ. If you find yourself wanting to restate a rule from the
canonical driver here, don't — fix the canonical driver instead; every harness reads it.

1. **Iteration.** Use `/loop` to keep the phase loop running, and arm a backgrounded
   command that *finishes* (e.g. `sleep 600`) as the continuation signal. A timer alone
   is not a continuation mechanism; a completing background task re-invokes reliably.
   Kill the armed heartbeat before any deliberate stop, or the stop is undone.
2. **Subagents.** `Agent` tool with `isolation: "worktree"`. Pass `model:` explicitly on
   every dispatch — a reviewer must never inherit the author's model. Route cheap sweeps
   to a small model, reviews to a *different family*, and the merge decision to a
   frontier model.
3. **Asking the operator.** `AskUserQuestion`.
4. **File references.** `@path`.
5. **Rules files.** Keep `CLAUDE.md` pointing at the canonical driver rather than
   restating it. `ddflow brief` supplies the per-task lesson retrieval that
   `CLAUDE.md` would otherwise have to mandate by prose.

Register the MCP server in `.mcp.json`:

```json
{ "mcpServers": { "ddflow": {
    "command": "python3", "args": ["-m", "ddflow", "--repo", ".", "mcp"],
    "env": { "PYTHONPATH": "vendor/ddflow" } } } }
```
