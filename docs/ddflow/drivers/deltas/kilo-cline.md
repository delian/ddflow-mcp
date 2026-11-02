<!-- ddflow:begin drivers/deltas/kilo-cline ddflow=0.2.10 fmt=1 sha=3b472cc902b6 -->
# Delta: Kilo / Cline / Roo

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration is background subagents; the operator is the exception, not the driver.**
   The `/ddflow-wave` slash command (`subtask: true`) runs the `ddflow-orchestrator`
   subagent as a BACKGROUND subtask, so the wave loop runs off the operator's main chat —
   the chat stays free and async notifications surface without `ctrl+b`. Never wrap the
   mission in a foreground session goal: `goal start` IS the loop that blocks the
   operator chat. The orchestrator subagent is the loop and runs it as data-driven waves:
   one read of `ddflow next --json`, claim each ready item, then fan the claimed items to
   `task` subagents — one claimed item each — mapping the `tier:fast|balanced|deep` hints
   to model choice, and after the merges land (or park, in the PR flow) re-read `next`
   for the next wave. The wave position goes to Kilo memory (the same `task`-subagent
   backdrop that keeps the waves running is the continuation; memory is only the
   positional state) so a compaction or a restart that re-runs `/ddflow-wave` resumes at
   the wave, never before it. Long gates (ci, unit_tests, reviews) run under background
   monitoring — they are waits, never stops. Stop only on a `question` or operator gate, or
   when the scope is empty; everything else — including a wait — carries the wave forward.
   On a runtime with no `task`-subagent run (Roo) there is no background loop: a one-shot
   invocation does ONE item directly — claim, gate, then merge or park — and prints
   `ddflow next` and `ddflow cadence` as the handoff for the next invocation. These loop
   facts are the `[orchestration]` record in `ddflow/harnesses/kilo.toml`; the command and
   subagent are EMITTED from `ddflow/templates/` by `ddflow adopt`, not hand-written.
2. **Subagents.** `task` tool, with worktree isolation where supported.
3. **Asking the operator.** The `question` tool.
4. **File references.** `@path`.
5. **Commands.** `/ddflow-wave` inlines the loop; `ddflow adopt` writes it under
   `.kilo/command/` and the orchestrator subagent under `.kilo/agent/`.

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
