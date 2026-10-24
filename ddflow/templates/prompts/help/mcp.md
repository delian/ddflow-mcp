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
  `implement`, `import-existing-project`, `research-companions`,
  `install-companions`, `bug-hunt`,
  `code-deduplication`, `code-clean`, `all-tests`. **Tools are things an agent calls;
  prompts are things you invoke.** `implement` is the unattended one: it drives the queue
  to completion, and for Claude Code `ddflow adopt` also writes `/implement`, which runs it
  under `/loop`.

Ask it anything about itself with `ddflow_help`, and ask what the rules are here with
`ddflow_workflow`.

## Lane workers: start them lean

Every ddflow schema a worker loads is first-turn context. For Claude Code, `ddflow adopt` writes the
lean role definitions (`.claude/agents/ddflow-<role>.md`) and `.ddflow/local/worker-mcp.json` (git-ignored: it
records this machine's launcher), the same server with `DDFLOW_MCP_TOOLS` set from `[mcp].worker_tools`
(`core` by default). Two ways to start a worker, measured on haiku (lean-start-inherit.md):

    claude --agent ddflow-<role> --strict-mcp-config --mcp-config=.mcp.json    # 9.8K first turn (implementer)
    claude --strict-mcp-config --mcp-config=.ddflow/local/worker-mcp.json     # 25.9K, a worker with no role

A role definition paired with the core-tier worker config is WORSE (implementer 17.2K), so pair it with the
all-tier `.mcp.json`. As is, with no flags, a session starts at 33.6K. Give the flag as `--mcp-config=FILE`: it
is variadic, and a space lets it read the prompt as a config path. `--strict-mcp-config` leaves out every other
MCP server (the claude.ai connectors cost ~8K deferred, ~150K with tool search off).
`ENABLE_TOOL_SEARCH=false` makes Claude Code send every schema up front; `ddflow doctor` says so when `[mcp].worker_tools` is `all`, and on `core` the cost stays small.
A tier hides schemas, never tools: every tool stays callable by name.

## What to put in AGENTS.md / CLAUDE.md

`ddflow adopt` writes this for you, as a managed block between
`<!-- ddflow:begin rules/work-queue ... -->` and `<!-- ddflow:end rules/work-queue -->`
markers. Your own prose around the block is preserved; re-running updates only what is
inside it.

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
