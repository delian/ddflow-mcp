# Organisation knowledge hub

Decision document for the task `KB-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-knowledge-hub`).

## D-knowledge-hub: Knowledge hub: git mirror and HTTP hub together, advisory org knowledge below project records, session summaries opt-in

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked for a shared remote knowledge base with its own protocol; note R-knowledge-hub.

**Decision.** Operator 2026-10-13 (AskUserQuestion, KB-decide): (1) Both transports TOGETHER (git mirror with an inbox, and the HTTP hub and feed), not git first; this differs from the recommendation. (2) Precedence: enforced org policy wins through a signed bundle, advisory org knowledge ranks below project operator records (policy bundle > project operator records > org-endorsed > project-promoted > agent). (3) Session-context summaries are opt-in per project, default off, after the publish scan. (4) Per-kind approvals (lesson 1, an agent approval only from another model family; rule 2 incl. a code owner; policy, skill, companion and config operator only); skills, subagents and companions only via signed bundles; first kinds lesson, decision, research/claim, rule, goal; a never-send-to-model sensitivity level; the protocol is named DKP (ddflow.kb/1).

**Consequences.** KB-feed and KB-hub no longer wait on the git transport and are built alongside it; the transports share the envelope and mirror.

**Alternatives rejected.** Git mirror first with the hub later; HTTP hub only; operator approves everything; org above project or project above org.

Governs: `docs/ddflow/decisions/knowledge-hub.md`
