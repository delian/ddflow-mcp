# Autopilot: levels, classes and protected verifiers

Decision document for the task `AP-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-autopilot`).

## D-autopilot: Autopilot: default level 1 (prepare), L3 only for doc fixes, patch bumps and flaky quarantine, protected verifiers blocked

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked for ddflow to propose work, act as architect and product manager and develop from stories and goals; note R-autopilot.

**Decision.** Operator 2026-10-13 (AskUserQuestion, AP-decide): (1) The default level for every class is 1 (prepare: the agent produces artefacts for proposals a person has ADOPTED, nothing merges and nothing enters the ready queue without the operator); this differs from the recommendation (level 0). Adoption of a proposal stays person-only. (2) Only the operator raises a class; a failed canary, revert or skipped gate demotes it automatically; the kill switch freezes scheduled proposal jobs and level 3. (3) Level 3 (execute and merge within budget when all gates pass) may ever apply only to doc fixes, patch-level dependency bumps and flaky-test quarantine, each switched on by the operator after its metrics. (4) Protected verifiers (oracle tests, gate configuration, review prompts, cost limits) are BLOCKED for autopilot work: outside the agent's write scope, edits operator-only. (5) Remaining defaults accepted: direction, licence- or security-impacting dependencies, public API and architecture changes capped at level 2; every proposal needs evidence, names its goal and expires; 10 open proposals per operator; architect and product workflows start at level 1, product at most level 2; budgets per proposal, day and class as knobs; calibration per proposer; the web-reading discovery job has no write beyond its proposal.

**Consequences.** AP-classes defaults to level 1; AP-protect is a block; AP-l3 is limited to three classes.

**Alternatives rejected.** Level 0 default; no level 3 ever; a wider level 3 set; warn-only verifier protection.

Governs: `docs/ddflow/decisions/autopilot.md`
