# Delta: Claude Code

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ. If you find yourself wanting to restate a rule from the
canonical driver here, don't — fix the canonical driver instead; every harness reads it.

1. **Iteration is a wave loop; the operator is the exception, not the driver.** `/implement
   [phase or task id]` — written to `.claude/commands/` by `ddflow adopt` — runs `/loop`
   (self-paced) on the `implement` workflow command, and ONE such session goal carries the
   whole mission: a phase, a bug batch or a wave. Each wave is one read of
   `ddflow next --json`: fan the ready set out to one task subagent per claimed item —
   mapping the `tier:fast|balanced|deep` hints to model choice — and after the merges land
   (or park, in the PR flow) re-read `next` for the next wave. The loop ends only two
   ways: the objective COMPLETES (everything landed) or it BLOCKS in a way only the
   operator can clear. `next` empty while work is in flight is neither — a wait, not an
   end (see 2). Mid-mission it stops ONLY where the question or approval flow needs an
   operator answer: `AskUserQuestion`, an operator approval
   (`ddflow ... --approval request`), a policy decision, an un-settleable review finding.
   Waits are never stops: see 2. The wave position goes
   nowhere extra: ddflow's own committed event log and the leases recompute the true wave
   state on every `ddflow next`, so a compaction or restart resumes the wave, never before
   it. Kill the armed heartbeat before any deliberate stop — `ScheduleWakeup` with
   `stop: true` — or the stop is undone. `/mcp__ddflow__implement` is the same command
   without `/loop`: it drives as far as one turn allows, with no wake-up to carry it on —
   a one-shot invocation falls back to ONE task, then prints `ddflow next` and
   `ddflow cadence` as the handoff. The continuation, each turn: a backgrounded command
   that *finishes* (e.g. `sleep 600`), then `ScheduleWakeup` re-passing the same `/loop`
   prompt. A timer alone is not a continuation mechanism; a completing background task
   re-invokes reliably. These loop facts are the `[orchestration]` record in
   `ddflow/harnesses/claude.toml`, emitted into this delta by `ddflow adopt`.
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
   every dispatch — a reviewer must never inherit the author's model. Map the
   `tier:fast|balanced|deep` hint `ddflow next --json` emits to the dispatched model;
   route reviews to a *different family*, and the merge decision to a frontier model.
   Launch with `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` in the environment (or
   `subagentPromptCacheTtl` in `.claude/settings.json`) so a subagent that waits past the
   5-minute default reuses its prompt cache instead of re-writing it (TE-subagent-ttl);
   `ddflow doctor` warns while it is still 5m.
4. **Asking the operator.** `AskUserQuestion`.
5. **File references.** `@path`.
6. **Rules files.** Keep `CLAUDE.md` pointing at the canonical driver rather than
   restating it. `ddflow brief` supplies the per-task lesson retrieval that
   `CLAUDE.md` would otherwise have to mandate by prose.

**Lane context caps (TE-context-caps).** `ddflow doctor` / `ddflow health` and the wake
brief name the rotation threshold `[session].agent_rotate_tokens` (default 250000) once an
agent's measured context passes it: the agent finishes the item, writes a hand-off note into
its evidence and stops, and the lead starts a fresh agent whose first call is `ddflow brief`.
The auto-compact window is the alternative a running session can still honour, controlled in
the harness's env block, not by ddflow:

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` — auto-compact window in tokens (100000–1000000);
  `session.claude_autocompact_window` names the value to put there.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` — percent override that LOWERS the window only;
  `session.claude_autocompact_pct_override` names it.
- `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` — set beside them (see 3), so a lane that
  waits past the 5-minute default reuses its prompt cache instead of re-writing it.

Register the MCP server in `.mcp.json`:

```json
{ "mcpServers": { "ddflow": {
    "command": "python3", "args": ["-m", "ddflow", "--repo", ".", "mcp"],
    "env": { "PYTHONPATH": "vendor/ddflow" } } } }
```
