# Delta: Goose

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration.** Within one session keep going: claim a task, run its gates, merge,
   then read `ddflow next` again for the next task — the session ends when an ask stops
   you or nothing is ready. Across sessions the handoff is the printed `ddflow next`
   and `ddflow cadence`.
2. **Subagents.** One claimed item each where Goose runs several.
3. **Asking the operator.** Ask and stop.
4. **File references.** Plain repository-relative paths.
5. **Where the rules live.** Goose reads **`AGENTS.md`** and then **`.goosehints`** by
   default. `ddflow adopt` writes the managed block into both, because `CONTEXT_FILE_NAMES`
   is configurable and a project that has narrowed it would otherwise silently stop seeing
   the rules. `ddflow doctor` reports either copy drifting.

## MCP registration

**No project-level file exists.** Goose keeps MCP servers as *extensions* in a user-level
YAML — `~/.config/goose/config.yaml`, or `%APPDATA%\Block\goose\config\config.yaml` on
Windows. Add ddflow there:

```yaml
extensions:
  ddflow:
    type: stdio
    name: ddflow
    enabled: true
    cmd: uvx
    args: ["ddflow-mcp"]
    timeout: 300
```

Note `cmd`, not `command` — Goose's key differs from every JSON-based agent's, so this
block is not interchangeable with the others.
