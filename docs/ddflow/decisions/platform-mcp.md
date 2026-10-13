# Agent platform APIs: Kilo MCP and similar

Decision document for the task `PM-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-platform-mcp`).

## D-platform-mcp: Platform APIs: Kilo MCP and similar enabled by default once the agent is onboarded; usage, profiles and cloud-agent backends; search-and-call facade

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked whether the new Kilo MCP server is worth supporting and for similar features of other agents; note R-platform-mcp.

**Decision.** Operator 2026-10-13 (AskUserQuestion, PM-decide): (1) The Kilo MCP entry and the similar platform entries are ENABLED BY DEFAULT once the agent is onboarded; this differs from the recommendation (written disabled, operator enables). Recorded reading: adopt writes the entry enabled for an onboarded agent, still with the tool allowlist (read and search by default), approvals for delete, billable and cross-org calls (the guard stays), the org always explicit, tokens never in the repo or log, returned text fenced; enabling can be switched off per project by a knob. (2) Platform APIs feed the governor and spend ledger (usage), profiles read-only first (write back only on consent), cloud agents as a lane backend with an approval or an approved template (D-control-planes), and ddflow offers a search-and-call facade next to the grouped tools, measured by the MCP budget reporter. First platforms: Kilo, then Devin, GitHub Copilot, Codex, Jules (alpha) and Cursor.

**Consequences.** PM-kilo writes the entry enabled for an onboarded Kilo agent; PM-guard stays mandatory before any enabled entry works; PM-eval keeps the refusal fixtures.

**Alternatives rejected.** Written disabled until the operator enables; meters only; lane backends only; not supported.

Governs: `docs/ddflow/decisions/platform-mcp.md`, `ddflow/**`
