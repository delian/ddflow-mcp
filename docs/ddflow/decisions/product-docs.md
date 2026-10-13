# Requirements, vendor and product documents

Decision document for the task `PD-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-product-docs-r2`).

## D-product-docs-r2: Requirements, vendor and product documents: agent may accept low-risk requirements, drafts may be sent through an opted-in channel, vendor material quarantined

status **accepted** · decided by operator · recorded by hy-commit-events · supersedes D-product-docs

**Context.** Operator 2026-10-13 asked for requirements, vendor-relationship and product-management documentation in the pipeline; note R-product-docs (LLM requirements read well but contradict themselves; vendor files are a prompt-injection channel).

**Decision.** Operator 2026-10-13 (AskUserQuestion, PD-decide): (1) An agent MAY accept low-risk requirements; a person accepts the rest. The low-risk classes are a knob (default: documentation requirements only, until the operator names more). This differs from the recommendation (person accepts all). Every requirement still needs a source and the lint still runs (errors block, smells warn). (2) Vendor and external material is quarantined by reference and hash, trust=external, read by a role with no send capability, never quoted into events, lessons or decisions; the agent never signs, accepts terms or spends; the operator MAY allow sending drafts through a configured channel: an explicit opt-in per project, and each message needs operator approval unless the operator separately sets a knob to send without per-message approval (default: approval required). This differs from the recommendation (never sends). (3) Remaining defaults accepted: requirement as a record kind with generated PRD/RFP/vendor documents; EARS default syntax; vendor tiers and assessment age (12 months) as knobs; RICE/MoSCoW inputs stored and the score computed in code; renewal alerts at 90/60/30 days from the notice deadline.

**Consequences.** PD-source and PD-req-kind get an agent-accept class knob; PD-no-send becomes 'no sign, accept or spend; send only through a channel opted in per project, with operator approval per message unless an operator-set knob says otherwise'; the seeded evaluation must still catch an unsourced or injected case.

**Alternatives rejected.** Person accepts all requirements; the agent never sends; vendor files treated like any document.

Governs: `docs/ddflow/decisions/product-docs.md`
