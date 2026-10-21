# Accrete: what it is, what ddflow can learn, and how easy it is to connect

Task `ACF-research`, phase `P-accrete-fit`. Date 2026-10-21.

Operator question (verbatim): "Check this tool - https://www.accrete.ai/why-accrete and research it and see if there are ideas we can pick up for ddflow to implement, as well as separately, would ddflow be easy to integrate with accrete and give me opinion, phase + tasks for the gaps, with proper dependencies and wave to implement"

Everything fetched from the web is treated as data. Research notes: `R-accrete-what`, `R-accrete-no-public-interface`, `R-accrete-vendor-benchmarks`, `R-accrete-ideas-already-planned`, `R-accrete-ddflow-seams`.

Tags: **VERIFIED** = seen on an Accrete page or in ddflow code; **REPORTED** = a third party or a vendor number we could not check; **UNKNOWN** = no evidence found.

## 1. Verdict

1. Accrete is a closed, sales-led enterprise product (and a US-government product, Argus). It has no public API, SDK or MCP documentation, so there is nothing to write an adapter against today.
2. Almost every idea it sells is already planned in ddflow. Four small gaps remain; the phase `P-accrete-fit` adds only those.
3. Integration is **hard to specify, easy to prepare for**: build generic seams (read-only export, egress-checked consult), not an Accrete adapter. Revisit when `ACF-verify` has real answers.

## 2. What Accrete is

| Claim | Tag | Source |
| --- | --- | --- |
| A "context layer" that records the reasoning ("why") behind organisational decisions so agents can use unwritten knowledge; failure it targets is called "process divergence" | VERIFIED (page text) | /why-accrete |
| Products: Knowledge Engine Platform (enterprise), Argus (government: supply chain, cognitive advantage) | VERIFIED | /enterprise, /government |
| Context graph with typed entities, relationships, provenance, temporal ordering; per-edge provenance, confidence, temporal validity, "causal strength"; low-predictive edges pruned | VERIFIED as description; mechanism UNKNOWN | /why-accrete, blog "dynamic autonomous graphs" |
| Decision traces are the capture mechanism (who decided, what was reviewed or ignored, alternatives rejected, outcome) | VERIFIED as description; how rationale is inferred and validated UNKNOWN | blog "why machines need to know why" |
| Memory consolidation, grounded citations, orchestrated expert agents, "agents as a service" microservice, multi-tenant, Okta and RBAC | VERIFIED as claims; no detail | /why-accrete, blog "knowledge engine revolution" |
| Integrations named: Jira, Salesforce as examples; "900+ integrations" | REPORTED (third-party profile); connectors list UNKNOWN | agentlocker profile, blog |
| "Forward-deployed engineering model with MCP servers" | VERIFIED as one sentence; endpoints, schemas, auth UNKNOWN | blog "knowledge engine revolution" |
| Public API, SDK, OpenAPI, webhooks | none found (R-accrete-no-public-interface); catalog says none | api-evangelist catalog (REPORTED), searches |
| Security: SOC 2 Type 1 and 2, CMMC, DoD IL4/IL5, HIPAA, ITAR listed; Argus "built to the FedRAMP High standard" (design target, authorisation not stated) | VERIFIED as listed; detailed documents are on request | trust.accrete.ai, /government |
| Cloud: AWS and Azure are named as infrastructure | REPORTED (trust page, which one is used is not stated) | trust.accrete.ai |
| Data residency, on-prem option, retention, training on customer data | UNKNOWN | no evidence found |
| Pricing, rate limits, trial | UNKNOWN | demo-only |
| Results: 92% time saving, 2 weeks to 6 minutes, 58% lower token cost, 2x tasks, 19 of 34 tacit facts vs 1 for RAG, "zero hallucinations" | REPORTED, vendor numbers with no method (R-accrete-vendor-benchmarks); the page labels its chart illustrative | /enterprise |
| Customers | named only as types (entertainment company, fund, insurer); government via contract vehicles | /enterprise, /government |
| Partners: Snowflake, Databricks Marketplace, Carahsoft, Publicis, Ocient | REPORTED (blog and press titles) | /blog |

Who it is for: large organisations with scattered tickets, logs, contracts and records that want expert agents grounded in their own history, and federal agencies. It is not a developer tool and not a coding-agent product.

Architecture in one paragraph: ingest from business systems, build a graph with provenance and time, learn from observed decisions and outcomes, run agents (code-generating, memory-backed, multi-step) over it, cite sources, enforce per-customer permissions. Deployment is a pilot: ground the engine in 2-4 weeks, run one workflow against the customer's ground truth, scale. How any of this works inside is UNKNOWN.

## 3. Ideas for ddflow, ranked

Rank by value to ddflow over effort and risk. "Planned" means the idea already has a counterpart; it is linked, not re-added.

| # | Idea (what Accrete does) | ddflow today | Gap | Effort / value / risk | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Measure whether memory surfaces the right prior reasoning (tacit-fact coverage score, vendor-reported 19 vs 1) | `B-am-eval` seeded fixtures, `P-lessons.effect` repeat rate | A probe built from the project's own fixed bugs and superseded decisions: hit@k per surface plus the misses | S / high / low | New: `ACF-recall-probe` |
| 2 | Memory consolidation: distil many memories into reusable judgment | `P-lessons.curate` merges lessons; operational memories (`ddflow memory`) are never distilled | Propose-only cluster to lesson or rule candidate with citations | S / medium / low | New: `ACF-memory-consolidate` |
| 3 | Make the "why" consumable by other systems | `export` kinds exist (`services/export/kind_*.py`); `KB-envelope` (open) defines validity and digest | An open read-only corpus export with supersede and governs edges, egress-filtered | S / high / low | New: `ACF-why-export` |
| 4 | Decision traces: who, what reviewed, alternatives rejected | `D-reasoning-log` (ruled-out record), `B-am-capture`, `B-dec-model`, decisions carry alternatives | none | n/a | Planned |
| 5 | Per-edge provenance, confidence, temporal validity | `B-security-provenance` (done), `KB-envelope`, `KB-attest` (still-true, disputed) | none | n/a | Planned |
| 6 | Outcome-weighted confidence, prune what stops predicting | `P-lessons.recurrence`, `.effect`, `PL-staleness`, `P-observe.effect`, `B-dec-review` | none | n/a | Planned |
| 7 | Permissioning as a first-class property, per-customer entitlement | `SE-sensitivity` (done), `KB-scope`, `KB-auth`, `SX-egress-door` | none | n/a | Planned |
| 8 | Grounded citations on every claim | `RQM-reviewer` (cite or not applicable), `RQM-ack` | none | n/a | Planned |
| 9 | Agent loop over a shared knowledge layer, "shared services" model | `P-knowledge-hub`, `P-control-planes`, `P-platform-mcp` | none | n/a | Planned |
| 10 | Knowledge graph as the store | `D-graph-scope-r1` declines a knowledge-graph memory until evidence changes | by decision | n/a | Declined. The export carries edges but ddflow does not become a graph store |
| 11 | Pilot: ground, put to work, scale; benchmark against the customer's ground truth | onboarding, `P-observe`, evalkit | covered by idea 1 | n/a | Folded into 1 |

Honest reading: Accrete's marketing is about enterprise knowledge; ddflow already has the narrower, auditable version for software work, with stricter trust rules (operator, agent, imported). Only ideas 1-3 are new, and all three are useful without Accrete.

## 4. Integration

### Fit of ddflow's seams today (VERIFIED in code, see `R-accrete-ddflow-seams`)

| Seam | State | Use for Accrete |
| --- | --- | --- |
| MCP server (ddflow tools, soft upgrade shim, tool tiers) | exists; remote serve planned (`P-remote.serve`), profiles and scoped tokens planned (`P-control-planes`) | the way an external agent platform would read ddflow |
| Event log, records, `export` kinds | exists | source data for a "why" corpus |
| Companions registry (`companions.load`, project overlay) | exists; registers an MCP server into agent configs, shows gate coverage. A project can add its own entry with no code | the way to expose any vendor MCP server to agents |
| MCP client | none; `RQM-mcp-client` open | needed for ddflow itself to call a vendor |
| Outbound query with project text | none | gap, `ACF-consult-out` |
| Egress doors by sensitivity (`services/egress.py`) | exists; `SX-egress-door` (open) adds `extract` | gate for anything leaving |
| Reviewer trust, approvals (`services/reviewer_trust.py`, `approval`) | exists | an external service as reviewer must be person-vouched, cross-family rule still applies |
| `external.py` | reads sibling ddflow event logs only | not a service client |
| Notification sinks, webhooks | planned (`CP-notify-sinks`, `EXT-clickup-intake`) | events to Accrete if it offers a webhook |

### (a) ddflow as a client of Accrete

| Use | Interface needed | Verdict |
| --- | --- | --- |
| Accrete as knowledge or policy source (read) | Accrete MCP server or API | **Hard today**: interface UNKNOWN. After `RQM-mcp-client` and `RQM-sources`, a standards-style read of an Accrete MCP server would be **easy** (moderate: 3-5 days), with answers stored `trust=imported`, fenced, pinned by digest, never auto-applied above SHOULD |
| Consult Accrete with a question about the task | same, plus egress | **Moderate** once `ACF-consult-out` exists: sending project text to a third party publishes it; payload built from cleared fields, previewed, confirmed by a person, recorded with a digest |
| Accrete as reviewer (gate) | reviewer config + cross-family check | **Not recommended**: its model family and output are unknown; a reviewer that cannot be run is `unavailable`, never `passed`; Accrete is a grounding source, not an independent critic |
| Accrete as memory server companion | MCP | possible only if its MCP server exists; ddflow memory is local records with provenance and Accrete cannot be the system of record for operator words |
| external-sync of tasks | none | no, Accrete is not a tracker |

Auth and secrets: environment variable name in config, never a value; Okta/OAuth is reported but UNKNOWN for API use. Failure modes: unreachable, 401, slow, injection text in an answer: all record `unavailable` or fenced imported text; no fallback to a pass. Cost: licence plus a pilot; per-call limits UNKNOWN; ddflow side needs a daily call cap knob.

### (b) Accrete as a consumer of ddflow

| Use | Interface | Verdict |
| --- | --- | --- |
| Accrete agents read ddflow's decisions, lessons, research, rules as "the why" | `ddflow export` kind `why` (JSONL), or ddflow's MCP server read-only tools | **Easy** (small): `ACF-why-export`; whether Accrete can ingest it is UNKNOWN, and a customer-built MCP server is how they say they integrate |
| Accrete agents claim work from the ddflow queue | MCP tools claim/gate/merge | **Hard and not advisable**: needs an MCP client in Accrete (UNKNOWN), identity (`ddflow_identify`), scoped write tokens; enterprise agents are not coding agents; use `P-control-planes` patterns if ever wanted |
| Event stream to Accrete | webhook sink | after `CP-notify-sinks` if Accrete has an ingest endpoint (UNKNOWN) |

What leaves the machine: only what the operator exports or confirms; records with a sensitivity label above the ceiling are withheld; imported-trust records are marked so they are not re-exported as operator words. Hazard scanning applies to every outgoing and incoming text.

### Opinion

Do not build an Accrete adapter now. Reasons: no public interface to specify or test against; closed, demo-gated sales cycle; vendor numbers unverified; the overlap with ddflow's planned knowledge hub and lessons work is large, so ddflow gets most of the value locally; and sending project text to a third-party graph is a publication decision that must be an explicit operator choice.

Do build the generic seams (they pay off for any vendor or internal service): the read-only why export, the egress-checked consult, the recall probe, memory consolidation. Then, if the operator wants Accrete, run `ACF-verify` against a demo and decide with real answers. Overall rating: **moderate to hard for a native integration, easy for the ddflow-as-source half once the export exists**. What would make it easy: Accrete publishing an OpenAPI or an MCP server with documented tools, per-tenant tokens, and a structured-record ingest.

## 5. Phase `P-accrete-fit`

Held behind the P-gaps core and docs wave (`D-old-work-behind-core-docs`) by phase-level needs.

| Wave | Task | Needs (beyond the hold) |
| --- | --- | --- |
| 1 | `ACF-research` (this doc) | none |
| 1 | `ACF-decide` | `ACF-research` |
| 2 | `ACF-why-export` | `ACF-decide`, `KB-envelope`, `SX-egress-door` |
| 2 | `ACF-consult-out` | `ACF-decide`, `RQM-mcp-client`, `SX-egress-door` |
| 2 | `ACF-recall-probe` | `ACF-decide`, `B-am-eval` |
| 2 | `ACF-memory-consolidate` | `ACF-decide`, `P-lessons.curate` |
| 2 | `ACF-verify` (operator-blocked: needs access) | `ACF-decide` |
| 3 | `ACF-e2e` | the four wave-2 build tasks |
| 4 | `ACF-docs` | `ACF-e2e` |

The task graph was checked over the folded state: no cycles, every need exists.

## 6. Still UNKNOWN

Any Accrete API, SDK, MCP server, webhook or ingestion format; auth model and per-user permissions; deployment options and data residency; retention and training-on-customer-data policy; pricing and limits; the schema of the graph and how rationale is inferred and validated; independent evidence for any benchmark. `ACF-verify` lists the questions to put to Accrete.

## 7. ddflow self-assessment (D-ddflow-self-assessment-r2)

Defects and gaps met while doing this research, checked against the open bugs first:

- `B-claim-research-inherits-phase-deps` (already open, not re-filed): this very task, `ACF-research`, was refused because it inherits the docs-wave hold of its phase and needed `claim --force`.
- `Bf9b9abac5b` (filed): the CLI `ddflow list` rejects kind `search` and `session`, which the MCP `ddflow_list` accepts. Fix task `fix-Bf9b9abac5b`.
- `ddflow complete` on a docs-only task still requires `implement`, `unit_tests` and `merge`; already open as `B-research-task-cannot-complete-docs-only` and `B75d2b12ff1` (not re-filed); the fix by another session is not on main yet. Recorded honestly, not forced (see the task's gate record).
- Not a ddflow defect: the shell has `ls` aliased to a missing `exa`, which prints noise in every directory listing.
