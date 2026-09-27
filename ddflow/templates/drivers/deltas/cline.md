# Delta: Cline

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **Iteration is operator-driven.** One task per request; print `ddflow next` and
   `ddflow cadence` as the handoff.
2. **Subagents.** Cline's `task` tool, one claimed item each.
3. **Asking the operator.** The `question` tool.
4. **File references.** `@path`.
5. **Where the rules live.** `.clinerules/` — a directory of Markdown files at the repo
   root, version-controlled. `ddflow adopt` writes **`.clinerules/ddflow.md`** with the
   managed block in it, alongside `AGENTS.md`, and `ddflow doctor` reports either one
   drifting. Your own files in `.clinerules/` are untouched. Cline also reads a global
   `~/.agents/AGENTS.md`.

## MCP registration

**No project-level MCP file exists.** Cline keeps MCP servers in a single GLOBAL settings
file outside the repository, so this is a per-machine step ddflow cannot do for you. Add:

```json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }
```

via Cline's MCP Servers panel (*Configure MCP Servers*), which is the reliable route —
the documented path for the file itself has been reported as disagreeing with what the
code reads, so prefer the UI over hand-editing.

The `kilo` target covers Kilo Code and Roo, which share Cline's lineage but do have a
project file.
