# Lean start: what subagents inherit, measured (LS-inherit-probe)

Closes the subagent-inheritance UNKNOWN of section 1 of [lean-start.md](lean-start.md) and re-measures other cells of it (the Opus difference and the core-tier cost stay open). Evidence labels as there: MEASURED =
read from `claude -p ... --output-format json` or `stream-json` usage on this machine (Claude Code 2.1.296,
model `haiku`, 2026-10-23); "first-turn input" is input + cache_creation + cache_read of the first request.
One run per cell unless stated, so +-0.3K noise; the two cells that were re-run (V2 and V6) each reproduced their first-run value exactly.

Probe project: a throwaway git repository, `ddflow init` and `ddflow adopt --agents claude` (CLAUDE.md 2,540 B,
the three lean agent definitions of LS-agents, `.ddflow/local/worker-mcp.json` of LS-worker-mcp-config), run from
that directory with the user's claude.ai connectors and plugins on, prompt "reply ok".

## 1. The session itself and its MCP choices

| id | variation | first-turn input |
|---|---|---|
| V0 | as is (connectors, project `.mcp.json` all-tier ddflow, tool search default) | 33.6K |
| V10 | `--strict-mcp-config`, no MCP at all | 24.2K |
| V12 | strict plus the project `.mcp.json` (ddflow all tier) | 25.6K |
| V1 | strict plus `worker-mcp.json` (ddflow core tier) | 25.9K |
| V17 | `--strict-mcp-config`, no MCP, `ENABLE_TOOL_SEARCH=false` | 39.6K |
| V7 | V0 with `ENABLE_TOOL_SEARCH=false` | 205.4K |
| V13 | V12 with `ENABLE_TOOL_SEARCH=false` | 52.7K |
| V8 | V1 with `ENABLE_TOOL_SEARCH=false` | 55.7K |

- ddflow's names cost 1.4K deferred (V12 against V10); the claude.ai connectors cost 8.0K deferred (V0 against
  V12) and ~150K undeferred (V7 against V13). `--strict-mcp-config` removes them: 8.0K saved with tool search on.
- Turning tool search off also inlines Claude Code's own built-in tool schemas (V17 39.6K against V10 24.2K), so
  ddflow's own undeferred cost is read against V17: all tier 13.1K (V13 against V17), a fifth of the 58.4K of
  lean-start.md probe L (the charter replaced a 3.1K instruction block and the tool-diet work has landed since;
  the split is UNKNOWN). The core tier does NOT undercut the all tier here: 16.1K (V8 against V17) against 13.1K,
  where lean-start.md measured core 54.7K against all 84.2K (probes K and L). UNKNOWN why a core-tier server weighs as much as the all tier in this run (one
  run each; the schema bytes/4 ESTIMATE says 8.8K against 26.6K tokens, which the run does not show).
- The hypothesis of lean-start.md (1M-window models do not defer) is REFUTED for haiku: its `modelUsage` reports a
  1,000,000-token window and V0 still deferred ddflow. Opus is not measured (no Opus spend).

## 2. A generated role definition as the main agent (`--agent`)

| id | variation | first-turn input |
|---|---|---|
| V3 | `--agent ddflow-researcher` | 10.7K |
| V2 | `--agent ddflow-implementer` | 12.6K |
| V4 | `--agent ddflow-reviewer` | 9.5K |
| V14 | implementer plus `--strict-mcp-config --mcp-config=.mcp.json` (all-tier ddflow) | 9.8K |
| V6 | implementer plus strict plus the CORE-tier `worker-mcp.json` | 17.2K |
| V15 | implementer with `DDFLOW_MCP_TOOLS=core` in the environment | 19.9K |
| V5 / V16 | researcher / reviewer plus strict plus worker config | 10.1K / 11.4K |
| V9 | V6 with `ENABLE_TOOL_SEARCH=false` | 17.2K |
| V11 | V5 with `ENABLE_TOOL_SEARCH=false` | 10.1K |

- Against 33.6K as is, a lean definition is 9.5K to 12.6K (the 25.4K to 10.4K of lean-start.md reproduced on
  this tree's real definitions). With tool search off the definitions cost the same (V9 = V6 = 17.2K; V11 = V5 = 10.1K): the
  allow-list, not deferral, bounds them.
- FINDING: for the IMPLEMENTER the core-tier worker config makes the role definition WORSE (V6 17.2K with strict
  plus worker config, V15 19.9K with the core tier in the environment, against V14 9.8K with strict plus the
  all-tier `.mcp.json` and V2 12.6K as is). The researcher shows no such effect (V5 10.1K against V3 10.7K) and the
  reviewer's cells are not like for like (V16 11.4K strict plus worker against V4 9.5K non-strict), so this is
  measured for the implementer only. The MECHANISM is UNKNOWN: a hypothesis is that the tools an implementer names
  outside the core tier (`release`, `research_add`, `review_triage`, `rule_search`, `rule_show`) are loaded in a
  costlier way when the server does not list them, but the researcher, which names nine such tools, does not show it.
  Until measured further, pair a role definition with the all-tier config (`--mcp-config=.mcp.json
  --strict-mcp-config`, V14) and use the core-tier worker config only for a worker with no role definition (V1).
  LS-worker-mcp-config's recipe should say so (follow-up filed).

## 3. What an Agent-tool subagent inherits

`stream-json` with `--verbose`: the first assistant message whose `parent_tool_use_id` is set, spawned from the
V0 session by the Agent tool.

| subagent | first-turn input |
|---|---|
| `general-purpose`, tool search on | 20.4K |
| `general-purpose`, `ENABLE_TOOL_SEARCH=false` | 184.8K |
| `ddflow-reviewer` | 16.2K |
| `ddflow-researcher` | 17.0K |
| `ddflow-implementer` | 17.2K |
| `ddflow-researcher` plus `omitClaudeMd: true` | 15.9K |

- A subagent inherits the parent's MCP servers and tools: with tool search off a general-purpose subagent carries
  every connector and ddflow schema (184.8K). A named `tools:` list bounds it (16K to 17K).
- It inherits CLAUDE.md: `omitClaudeMd: true` saved 1.1K with a 2.5 KB CLAUDE.md. It also costs the subagent the
  project's own instructions, so it is an operator decision (follow-up filed), not a default.
- Run as a subagent a definition costs 16K to 17K, more than as the main agent (9.5K to 12.6K): the fixed Claude
  Code subagent prompt and built-ins are in the former. The memory index is not separated: UNKNOWN.
- The Agent tool sometimes answered itself instead of spawning (3 of 9 prompts), so a cell needed up to three
  tries; only runs that spawned are counted.

## 4. What this changes

1. LS-agents' definitions are the right default lever and measure as designed.
2. Do not pair a role definition with the core-tier worker config: `worker-mcp.json` is for workers WITHOUT a role
   definition; the recipe and the docs say which (LS-worker-recipe-roles).
3. `--strict-mcp-config` is worth 8.0K with tool search on and ~150K with it off, for any session.
4. Set `ENABLE_TOOL_SEARCH` knowingly: off, a general-purpose subagent starts at 185K.
5. `omitClaudeMd` is an available 1K+ lever for operators (LS-agents-omit-claudemd).
