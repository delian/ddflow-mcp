# Decision: always-loaded context budgets (D-session-start-budget)

Source: QD-decide-always-on-budgets, queue dedupe review 2026-10-23. Operator answer 2026-10-23, verbatim: "One total budget and separately a budget per agent type - aka one budget for claude code, separate budget for codex, third budget for copilot, cursor and so on. The idea is - if we have many subscriptions and many LLM, to be able to manage the budget separately per subscription, or llm target, while at the same time also have a global budget".

## Decided
- A GLOBAL total budget: `[session].context_budget_tokens` (exists, default 40000).
- PLUS a budget PER AGENT TYPE (harness / subscription / LLM target): `[session.context_budget]` with `global` and one key per agent type (claude, codex, copilot, cursor, dsh, ...; the agent-type names are the `ddflow harness list` names). Each is an independent knob and layers by D-config-layer-order, so a company can set its own and a user can set theirs per subscription.
- One global knob: `[session].context_budget_tokens` is the global budget; `[session.context_budget].global` is its alias (same value, never a second quantity), and the table otherwise holds only the per-agent-type keys. Formula: `effective = min(context_budget_tokens, context_budget.<agent type>)`.
- The effective budget of a session is the MIN of the global budget and its agent type's budget (the lead's reading; it does not contradict the answer: the global is the ceiling over all targets, a per-type value tightens for one target; an unset type inherits the global).
- Allocation inside a budget follows the recommended default that the operator did not contradict: SessionStart (<=1K chars) carries the contract plus at most ONE dynamic line (resume, else pending approvals, else upgrade); requirements/policy charters, rules and recovery detail are fetched by brief on request or at claim; GP-brief supersedes the separate requirements charter line once graded policies land; SE-mcp-budget and P-change-surface.context-budget are rewritten to the MTD per-profile numbers.
- `doctor` and the session-start-cost meter (LS-measure) report per target: global, and per agent type the measured cost against that type's budget.
- Later, "budget per subscription / LLM" may cover spend and usage budgets too (usage ledger, B-gap-spend): linked, NOT built here.

## Work
LS-budget-per-agent (P-lean-start, needs LS-measure). LS-profile-select, LS-launch-brief-budget, LS-docs, LS-e2e, MTD-profiles and MTD-budget-test are amended to read the effective (per agent type) budget, not one number.
