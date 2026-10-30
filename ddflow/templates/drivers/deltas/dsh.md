# Delta: dsh (DeepSeek Harness)

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ. Facts are from `docs/ddflow/research/dsh-probe.md`
(dsh 0.2.1-rc.2, a developer preview that promises breaking changes).

1. **Iteration.** A parent dsh process is the loop: it reads `ddflow next`, fans the
   ready items out to native `subagent` / `subagent_fork` runs (one claimed item each),
   waits on them, then re-reads `next` for the next wave. Headless is one task per
   process, so drive it from a shell `while` that exits when `next` ends 2 with nothing
   ready; the printed `ddflow next` + `ddflow cadence` output is the handoff where no
   wrapper runs.
2. **Subagents.** dsh has native `subagent` and `subagent_fork` tools. Give each one claimed
   item; a subagent shares your MCP connection, so pass `as_agent` on every call.
3. **Asking the operator.** Ask and stop. Headless has no answerer: an ask is refused and the
   tool returns an error to the model; the process still exits 0.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** `AGENTS.md` and `CLAUDE.md` are both read, per directory from
   the project root down to the working directory, plus `AGENTS.local.md`, `CLAUDE.local.md`
   and `$DSH_HOME/AGENTS.md`. The budget is 64 KiB; broader files are dropped before the
   most specific one is truncated, so keep the root files small.
   Skills are read from `.agents/skills` (shared with other agents) and also `.dsh/skills`.
6. **Declare your model.** dsh runs any model. Review independence comes from the model
   declared at session start, not from the harness: say which model this session runs.
7. **Tool names.** dsh's own tools are lowercase (`bash`, `read`, `write`, `edit`, `grep`);
   MCP tools are `mcp__ddflow__ddflow_*`. A hook matcher or permission rule written for
   Claude Code's `Bash` or `Edit` will not match.

## MCP and hooks registration

dsh has **no project MCP file**. ddflow writes one overlay it owns,
`.ddflow/harness/dsh.cordis.yml`, and you must launch dsh with it:

    dsh --profile <p> --patch .ddflow/harness/dsh.cordis.yml

Without `--patch` there is no ddflow, and no error. The row looks like:

```yaml
- insert:
    - {"id": "ddflow", "name": "@deepseek-ai/dsh-mcp-client", "config": {"serverName": "ddflow", "transport": "stdio", "command": "uvx", "args": ["ddflow-mcp"]}}  # ddflow-row:ddflow
```

dsh scrubs the environment of stdio servers (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*` and
every `DSH_*` are dropped): put what ddflow needs in the row's `env`.

The hooks come from the experimental `@deepseek-ai/dsh-hooks-claude-code` bridge, which must
be the version that matches dsh (`@next`, not `latest`) and is named in the overlay by the
absolute path of its `lib/index.js`. If the bridge fails to load, dsh starts without hooks and
prints one warning on stderr: check `ddflow verify`, not the model. The events are
SessionStart, UserPromptSubmit, PreToolUse, PostToolUse and Stop (the bridge also documents
SubagentStart and SubagentStop, which ddflow does not map and the probe did not exercise);
there is no pre-compaction or session-end event. `transcript_path` is always empty.

## What leaves the machine

Prompts, file contents and tool output go to the configured model endpoint. dsh's base
composition also mounts a feedback-gated telemetry exporter (`DSH_TELEMETRY_MODE`); `web_fetch`
and `web_search` tools are on by default in the headless profile.
