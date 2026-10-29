<!-- ddflow:begin drivers/deltas/kilo-cline ddflow=0.2.9 fmt=1 sha=dd392465f93b -->
# Delta: Kilo / Cline / Roo

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration is a goal loop; the operator is the exception, not the driver.** Drive a
   whole phase, a bug batch or a wave under ONE Kilo session goal (`goal start` with the
   objective); the goal resumes itself until the objective completes or blocks, and
   `goal_report` closes it. Each wave is the ready set of `ddflow next --json`: fan it
   out to `task` subagents — one claimed item each — mapping the `tier:fast|balanced|
   deep` hints to model choice, and after the merges land (or park, in the PR flow)
   re-read `next` for the next wave. Persistent state (where the wave is up to) goes to
   Kilo memory so a compaction or restart resumes at the wave, never before it. Stop
   ONLY where the question tool says so: operator approvals
   (`ddflow ... --approval request`), policy decisions, an un-settleable review
   finding. Long gates (ci, unit_tests, reviews) run under background monitoring or a
   scheduled wakeup INSIDE the goal — they are waits, never stops. Where the runtime
   has no goal loop (Roo, or a one-shot invocation), fall back to ONE task, then print
   `ddflow next` and `ddflow cadence` as the handoff.
2. **Subagents.** `task` tool, with worktree isolation where supported.
3. **Asking the operator.** The `question` tool.
4. **File references.** `@path`.
5. **Commands.** A slash command should inline BOTH files — the canonical driver and
   this delta — because a link is only followed if the agent chooses to follow it.

MCP registration in `.kilo/kilo.json`:

```json
{ "mcp": { "ddflow": {
    "type": "local", "command": ["python3", "-m", "ddflow", "--repo", ".", "mcp"],
    "enabled": true } } }
```

The key is `mcp`, not `mcpServers`: Kilo silently ignores an `mcpServers` block. `command`
is ONE array including the arguments. `timeout`, if you add one, is in MILLISECONDS
(default 10000). Source: https://kilo.ai/docs/automate/mcp/using-in-cli
<!-- ddflow:end drivers/deltas/kilo-cline -->
