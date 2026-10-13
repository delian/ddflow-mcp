# Shared engines

Decision document for the task `SE-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-shared-engines`).

## D-shared-engines: Shared engines: one approvals engine, one waiver record, record kinds and event kinds as data, MCP budget as a warning, evaluation harness, sensitivity labels, packs off until enabled, principles blocked at the gate

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked for a whole-roadmap review (note R-roadmap-review) and for every phase to follow D-universal-principles.

**Decision.** Operator 2026-10-13 (AskUserQuestion, SE-decide): (1) ONE approvals and questions engine, built early and local first; P-remote.ask becomes only its transport. (2) ONE waiver record (scope, reason, approver through the approvals engine, expiry) for decisions, safeguards, goals, tests and gates. (3) Simple record kinds are declared as TOML data, Python only for kinds with behaviour; users may define kinds. (4) Event kinds are declared as data and an unregistered kind FAILS CI. (5) MCP tool-list budget: REPORT ONLY, a warning, never failing the build (the recommendation was a CI-failing ratchet); each declared command still reports its cost and joins an existing action group, and per-role profiles stay. (6) A workflow is enabled by default only after it catches its planted-fault set in the shared evaluation harness. (7) One sensitivity vocabulary (public, internal, restricted, confidential), scan at write, restricted and confidential never sent to a model unless allowed. (8) Workflow packs are off until enabled; onboarding offers them by project signals; never installed without consent; org-bundle format. (9) The universal principles (D-universal-principles) are enforced by BLOCKING at the standards gate: conformance gaps, a second implementation of existing behaviour and hand edits to README.md, ddflow/config.py, ddflow/surfaces/mcp.py and ddflow/core/events.py fail the gate unless listed as an exemption with a reason; exemptions only shrink. (10) Remaining defaults accepted: actor model operator, trusted bot and agent with model family and capabilities as config; cost preview before any workflow; onboarding offers packs in one conversation; SE-globs gives the new tasks literal globs; a claim-time hint lists existing helpers; the workflow-template engine is layered shipped < org < project < local with show, edit, reset, diff and explain.

**Consequences.** SE-mcp-budget is a reporter and a warning, not a CI gate; SE-conformance and SE-reuse-guard block at the standards gate; the other engines land before their consumers (P-shared-engines edges).

**Alternatives rejected.** Per-phase approvals and waivers; Python descriptors for every kind; a CI-failing MCP ratchet; warn-only or ratchet-only enforcement of the principles; packs on by default.

Governs: `docs/ddflow/decisions/shared-engines.md`, `ddflow/**`
