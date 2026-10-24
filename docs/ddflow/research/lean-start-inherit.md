# Lean start: what subagents inherit, measured (LS-inherit-probe)

Closes the subagent-inheritance UNKNOWN of section 1 of [lean-start.md](lean-start.md) and re-measures other cells of it (the Opus difference and the core-tier cost stay open). Evidence labels as there: MEASURED =
read from `claude -p ... --output-format json` or `stream-json` usage on this machine (Claude Code 2.1.296,
model `haiku`, 2026-10-23); "first-turn input" is input + cache_creation + cache_read of the first request.
One run per cell unless stated, so +-0.3K noise; the two cells that were re-run (V2 and V6) each reproduced their first-run value exactly.

Probe project: a throwaway git repository, `ddflow init` and `ddflow adopt --agents claude` (CLAUDE.md 2,540 B,
the three lean agent definitions of LS-agents, `.ddflow/local/worker-mcp.json` of LS-worker-mcp-config), run from
that directory with the user's claude.ai connectors and plugins on, prompt "reply ok".

**Correction (2026-10-24, bug B07c4286c92).** The first version of this note labelled the project's
`.mcp.json` runs "all tier". They were the default `lean` PROFILE (15 tools), and the first role definitions
allow-listed flat tool names the lean profile does not list, so the implementer, reviewer and researcher
cells below were measured with a crippled allow-list (no gate, bug, session tool). The definitions now name
both spellings and the tables hold the re-measured numbers; the earlier ones are not reported.

## 1. The session itself and its MCP choices

| id | variation | first-turn input |
|---|---|---|
| V0 | as is (connectors, project `.mcp.json`: ddflow's default lean profile, tool search default) | 34.0K |
| V10 | `--strict-mcp-config`, no MCP at all | 24.2K |
| V12 | strict plus the project `.mcp.json` (ddflow lean profile, 15 tools) | 26.0K |
| V1 | strict plus `worker-mcp.json` (ddflow core tier, 36 tools) | 25.9K |
| V17 | `--strict-mcp-config`, no MCP, `ENABLE_TOOL_SEARCH=false` | 39.6K |
| V7 | V0 with `ENABLE_TOOL_SEARCH=false` | 205.4K |
| V13 | V12 with `ENABLE_TOOL_SEARCH=false` | 52.7K |
| V8 | V1 with `ENABLE_TOOL_SEARCH=false` | 55.7K |

- ddflow's lean-profile names cost 1.8K deferred (V12 against V10); the claude.ai connectors cost 8.0K deferred (V0 against
  V12) and ~150K undeferred (V7 against V13). `--strict-mcp-config` removes them: 8.0K saved with tool search on.
- Turning tool search off also inlines Claude Code's own built-in tool schemas (V17 39.6K against V10 24.2K), so
  ddflow's own undeferred cost is read against V17: the lean profile 13.1K (V13 against V17), the core tier 16.1K
  (V8 against V17), against the 58.4K of lean-start.md probe L for all 123 tools. The lean profile (15 tools) is
  lighter than the core tier (36): the earlier "core is not lighter than all" worry was a mislabel, V13 was lean.
- The hypothesis of lean-start.md (1M-window models do not defer) is REFUTED for haiku: its `modelUsage` reports a
  1,000,000-token window and V0 still deferred ddflow. Opus is not measured (no Opus spend).

## 2. A generated role definition as the main agent (`--agent`)

| id | variation | first-turn input |
|---|---|---|
| X3 / X4 / X2 | `--agent ddflow-researcher` / `-reviewer` / `-implementer`, as is | 17.3K / 16.1K / 19.9K |
| X14s / X14r / X14 | the same plus `--strict-mcp-config --mcp-config=.mcp.json` (lean profile) | 14.5K / 13.4K / 17.2K |
| X5 / X16 / X6 | the same plus `--strict-mcp-config` and the CORE-tier `worker-mcp.json` | 10.7K / 11.9K / 17.8K |
| X9 | implementer, strict, lean profile, `ENABLE_TOOL_SEARCH=false` | 17.2K |

- Against 34.0K as is (V0), a role definition is 10.7K to 19.9K. With tool search off the definitions cost the same
  (X9 = X14): the allow-list, not deferral, bounds them.
- The best pairing is the role definition with `--strict-mcp-config` and the core-tier worker config for the
  researcher and the reviewer (10.7K, 11.9K), the implementer is within 0.6K either way (17.8K, 17.2K): the
  worker config is the recommendation, and `ddflow adopt` writes it. The first version of this note read the
  implementer difference as "the core tier makes a role worse"; with a functional allow-list it does not.
- The definitions allow-list each tool by BOTH names, the flat name the core tier lists and the family name the
  default lean profile lists: a role asked to call `mcp__ddflow__ddflow_gate` reaches it (it then needs a
  permission, which `claude -p` cannot give). Tools a profile does not list (lean has no `lease`, `reviewers`
  or `decision` family) are reached with the CLI from Bash, as the body of each definition says.

## 3. What an Agent-tool subagent inherits

`stream-json` with `--verbose`: the first assistant message whose `parent_tool_use_id` is set, spawned from the
V0 session by the Agent tool.

| subagent | first-turn input |
|---|---|
| `general-purpose`, tool search on | 20.4K |
| `general-purpose`, `ENABLE_TOOL_SEARCH=false` | 184.8K |
| `ddflow-reviewer` (current definition) | 17.2K |
| `ddflow-implementer` (current definition) | 18.2K |
| `ddflow-researcher`, first definition (crippled allow-list) | 17.0K |
| the same plus `omitClaudeMd: true` | 15.9K |

- A subagent inherits the parent's MCP servers and tools: with tool search off a general-purpose subagent carries
  every connector and ddflow schema (184.8K). A named `tools:` list bounds it (17K to 18K).
- It inherits CLAUDE.md: `omitClaudeMd: true` saved 1.1K with a 2.5 KB CLAUDE.md. It also costs the subagent the
  project's own instructions, so it is an operator decision (follow-up filed), not a default.
- Run as a subagent a definition costs 17K to 18K, about the same as the main-agent cells with the connectors
  left in (16.1K to 19.9K, section 2). The memory index is not separated: UNKNOWN.
- The Agent tool sometimes answered itself instead of spawning (3 of 9 prompts), so a cell needed up to three
  tries; only runs that spawned are counted.

## 4. What this changes

1. LS-agents' definitions are the right default lever: 10.7K to 17.8K against 34.0K as is, with a FUNCTIONAL
   allow-list (the first version was not, B07c4286c92).
2. Start a worker with `--strict-mcp-config --mcp-config=.ddflow/local/worker-mcp.json`, adding
   `--agent ddflow-<role>` when a role fits; the recipe and the help page say so.
3. `--strict-mcp-config` is worth 8.0K with tool search on and ~150K with it off, for any session.
4. Set `ENABLE_TOOL_SEARCH` knowingly: off, a general-purpose subagent starts at 185K.
5. `omitClaudeMd` is an available 1K+ lever for operators (LS-agents-omit-claudemd).
6. A measurement is only as good as what it ran: check what the probe actually listed (the lean profile
   hid most of the flat names) before reading a delta as a mechanism.
