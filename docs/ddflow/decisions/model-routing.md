# Model routing

Decision document for the task `GV-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-model-routing`).

## D-model-routing: Model routing: an optimize_for preset, safe-point switching with a cost estimate, role routing, escalation, blocking guardrails, recorded and evaluated

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-14 asked about Copilot's 'Optimize for' (Efficiency, Balance, Intelligence) and what to improve in the planned governor; note R-model-routing.

**Decision.** Operator 2026-10-14 (AskUserQuestion, GV-decide), every answer as recommended: (1) An optimize_for preset (efficiency, balance by default, intelligence) compiled into the governor's knobs, never lowering floors; a mid-run change shows the cache-cost warning and applies at the next safe point; per project and per org; settable by file, CLI, MCP and prompt. (2) Model and effort switches happen only at safe points (claim, after compact or checkpoint, stage boundaries) with an estimated switch cost, also shown in the workflow cost preview; routing is by role and stage (planner strong, implementer at the predicted tier, triage cheapest, reviewer floored). (3) Cascade with escalation on gate failures, loops, a substantive review rejection and the context ladder, with hysteresis and a cap on switches per task; guardrails BLOCK dispatch below the reviewer floor or the security pin, forbid a downgrade after escalation, and never let a harness-side Auto count as an independent reviewer; waivers only through the waiver engine. (4) The model that actually ran and the cache tokens are recorded per step; replay and A/B evaluation come before a policy is enabled by default; the preset maps to Copilot Auto and similar routers where a door exists, else the effective model is unknown; remaining defaults accepted: advisor-style escalation where the harness supports it, ACP config options for switching, a regret and yield report with a per-tier break-even.

**Consequences.** GV-preset, GV-switch-cost, GV-roles, GV-escalate, GV-guardrails (block), GV-step-cost, GV-eval and GV-harness-routers implement this inside P-governor.

**Alternatives rejected.** No preset; switching whenever the governor wants; route by task only; guardrails that only warn; no automatic escalation; cost-only recording without an evaluation gate.

Governs: `docs/ddflow/decisions/model-routing.md`, `ddflow/**`
