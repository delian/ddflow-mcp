# Constellation: sister projects, specs and topologies

Decision document for the task `CN-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-constellation`).

## D-constellation: Constellation: shared spec repo first, gates that warn, optional submodule umbrella, monorepo one log, agent-approved baselines

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked how sister projects (microservices, submodules, monorepo or split repos) share requirements, specs, user stories, APIs, tests and integration documents and how an update propagates; notes R-constellation and the topology research.

**Decision.** Operator 2026-10-13 (AskUserQuestion, CN-decide): (1) Transports: shared spec repo first, then a read-only git mirror of a sibling's event log, the hub later; spec identity is a model digest plus a content digest and pins are by digest; events reference sibling events by digest and never embed them. (2) Strictness: the breaking-change gate, the change-request merge gate, the non-owner-edit refusal and the can-i-release gate only WARN (findings shown at the gate, in review and in the brief); this differs from the recommendation, which was to block. The break class still comes from a checker, and a gate can be set to block per project or per spec lifecycle as a knob. (3) Topologies: the submodule umbrella is supported but optional (a child with its own .ddflow is a separate project with a submodule-of pointer, cross-boundary leases are refused, work is paired items); a monorepo is one log with a project attribute from projects.toml; propagation is pull-first through constellation sync with push optional; change requests are proven inside a monorepo first, then across repos. (4) Baselines: a spec baseline is APPROVED BY THE AGENT when its checks pass, with no human in the loop; this differs from the recommendation. Recorded reading: the approval policy is a per-baseline-kind setting; interface and spec baselines default to agent-approved on passing checks, while requirement baselines keep their own policy (D-product-docs-r3 person acceptance) because they share the engine but not the approval default; every approval is an event with the approving agent and its checks. Remaining defaults as recommended: adapters OpenAPI and protobuf first, then ASN.1, schema formats, YANG, DBC and register maps; imported requirement tools stay read-only with their source ids; deprecation window 90 days as a knob.

**Consequences.** CN-breaking-gate, CN-cr-gate, CN-matrix and CN-roles default to warn with a block option; CN-baseline gets a per-kind approval policy with the agent default; the seeded evaluation checks that the warnings are visible; the SE-conformance enforcement of the universal principles is unchanged.

**Alternatives rejected.** Blocking gates; human-approved baselines; hub first; one log per sub-project; no submodule support.

Governs: `docs/ddflow/decisions/constellation.md`, `ddflow/**`
