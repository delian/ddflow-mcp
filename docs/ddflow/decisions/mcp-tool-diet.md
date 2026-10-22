# MCP tool diet: families, profiles and a search/call facade

Decision D-mcp-tool-diet (operator, 2026-10-22, AskUserQuestion on MTD-decision). It extends
D-mcp-tool-families (operator, 2026-10-19) and completes the supersession of D-mcp-surface (1)
"no tool is renamed or merged" (that point lives on in D-mcp-surface-r2, itself superseded).
Evidence: docs/ddflow/research/mcp-tool-diet.md (sections 4, 5, 9).

## Context

Clients cap the tools they accept (128, 100, 64, some 40) and the cap is shared by every MCP
server. The flat surface is 123 tools / ~104 KB; only 38 were ever called.

## Decision

Chosen option: "Families with an action + search/call facade for tiny windows".

1. 24 advertised tools: 9 hot singles plus 15 noun families with an `action` enum named like the
   CLI `noun verb` (`ddflow_gate action=record`). Generated from the declared-command matrix,
   which stays the single source (CLI/MCP parity).
2. Old tool names stay callable as hidden aliases (not listed), with a once-per-session
   deprecation note, for one deprecation window (two releases, as in D-mcp-tool-families), then
   MTD-sunset removes them.
3. Per-client profiles chosen from the descriptor cap and `initialize.clientInfo.name`, never by
   a runtime call (`listChanged` stays false): `lean` 15 tools, `families` 24 (the default),
   `hot+families` 36. Flat tiers stay opt-in.
4. For the smallest caps (below 24) the 2-tool `ddflow_search` / `ddflow_call` facade
   (PM-search-call) is the escape hatch; no second dispatcher.

## Alternatives rejected

- Flat 123 tools with profiles only: best case still 36+ tools and the per-tool envelope stays.
- Single dispatcher tool plus help: least proven, loses per-action schema validation.

## Consequences

Hooks, permission files and drivers that name old tools rely on the alias window; permissions
become action-aware (MTD-permissions). Call accuracy of action-enum tools is unproven:
MTD-accuracy-eval still gates the rollout and the next-hints default.
