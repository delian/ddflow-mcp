# Driving it as an MCP server

    ddflow mcp        newline-delimited JSON-RPC over stdio, until EOF

You rarely run that by hand — an agent launches it. `ddflow adopt` writes the launch
entry into each agent's own config: `.mcp.json` (Claude Code), `.gemini/settings.json`,
`.codex/config.toml`, `.vscode/mcp.json` (Copilot), `.kilo/kilo.json`,
`.cursor/mcp.json`. Existing servers in those files are preserved, never overwritten.

## What an agent sees the moment it connects

- **Instructions**, returned inside the `initialize` result itself — so there is no
  call to forget. They are state-aware: what is ready, what is in flight, what setup is
  missing, whether this project has history worth importing, whether an import was left
  unfinished. The text is `.ddflow/prompts/mcp_instructions.md` if you have ejected it.
- **Tools** — every CLI command, one tool each.
- **Resources** — `ddflow://board`, `ddflow://brief`, `ddflow://lessons`,
  `ddflow://research`, as markdown.
- **Prompts** — the workflow commands, which a client turns into slash commands:
  `import-existing-project`, `bug-hunt`, `code-deduplication`, `code-clean`,
  `all-tests`. **Tools are things an agent calls; prompts are things you invoke.**

Ask it anything about itself with `ddflow_help`, and ask what the rules are here with
`ddflow_workflow`.

## What to put in AGENTS.md / CLAUDE.md

`ddflow adopt` writes this for you, as a managed block between
`<!-- DDFLOW:BEGIN -->` and `<!-- DDFLOW:END -->` markers. Your own prose around the
block is preserved; re-running updates only what is inside it.

If you are writing it by hand, the four things that must be in it are:

1. **Start every session with `ddflow_brief`** (or `ddflow brief` in a shell).
2. **Claim before you edit.** `ddflow_next` → `ddflow_claim` → work in the worktree it
   creates. The pre-commit hook enforces this.
3. **The loop:** `ddflow_gate_status` → satisfy each gate → `ddflow_complete` →
   `ddflow_merge`.
4. **The exit codes**, and that `2` is not success.

Without that block an agent sees the tools and has no idea it is meant to reach for
them before editing. The block is what makes the queue authoritative rather than
optional.

## A caveat worth knowing

The instructions are computed once, at connect. A workflow changed mid-session takes
effect immediately for every tool call — config is re-read on each one — but the text
the agent was handed is stale. Tell it to call `ddflow_workflow`, or restart the
server.

See also: `ddflow help cli` for the same workflow without an agent.
