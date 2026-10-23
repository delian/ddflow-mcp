# Decision proposal: common core, per-harness and per-model drivers that are mostly data

State: **DECIDED** 2026-10-23 (answers below; operator chose option C with a plugin SDK now). Phase P-drivers, task DRV-research, 2026-10-23.
Evidence and labels: [research/driver-architecture.md](../research/driver-architecture.md) (V verified, R reported, U unknown).
Operator request (verbatim, 2026-10-23): "Do you create drivers for every coding agent and model in order to keep the core code common and only adjust depending on which agent and model we communicate with and probably we could even adjust to the differences mostly with confirmation than code so it will be easier to adjust to future changes and coding agents? Respectively the same for agents that ddflow instantiate as subprocesses (if we plan that in the future)? Does this make sense? Can you research it?" ("confirmation" read as configuration.)
Related, not duplicated: D-acp-r2, D-control-planes, D-config-layer-order, D-extension-seams, D-mcp-new-nouns, D-everything-configurable, D-model-routing, D-dsh-telemetry; HX-facts-core, P-agent-parity.descriptor-v2, DX-descriptor-vocab-dsh, ATF-descriptor-fields, CC-inventory, TE-limit-aware-routing, CP-backend-iface, AC-backend, AC-prefer, CM-packs.

## Finding in one paragraph

Yes, and ddflow is partly there: 23 descriptors, a strict loader, three-valued facts and a conformance suite cover the harness axis for files, hooks and identity. Missing: a model axis with a combination rule, any subprocess driver in code (seven open tasks would each write their own launcher), one fact record with version range and expiry (four dialects today), and extension from outside the package (the loader reads only the shipped directory). 110 hard-coded agent-name sites remain (ratchet baseline), of which 65 are data kept as Python literals, 26 compat shims, 9 model-axis names, 10 legitimate driver code.

## Proposals

| Id | Proposal | Task | State |
|---|---|---|---|
| D-DRV-1 | Three data registries: harness (`ddflow/harnesses`), model family and model (`ddflow/models`, extends CC-inventory), provider route; effective capability = `effective(harness, model, route)` with: mechanism from the harness, numbers from the model, route overrides only where it changes semantics, a sparse pair table, caps combine by min and prohibitions by any, unknown never reads as supported | DRV-model-catalog, DRV-combine | DECIDED |
| D-DRV-2 | A closed set of named engines interprets descriptor data (E1 fieldmap, E2 emit variants, E3 mcp_shape, E4 render_argv, E5 stream_map, E6 limit_detect, E7 transcript_map for flat JSONL, E8 safe probe); code stays only for writers, transport protocols, non-flat transcripts, stateful stream meaning, tiered price arithmetic; success metric: agents added with zero Python | DRV-engines | DECIDED |
| D-DRV-3 | One fact record `{value, status, source, retrieved, versions, expires, probe, by}` for descriptors, models, routes and the client table; unknown degrades to the lowest-common-denominator behaviour and is reported; doctor flags expiry and installed-version drift; observation outranks documents | DRV-facts (on HX-facts-core) | DECIDED |
| D-DRV-4 | One `Lane` driver interface (`start, events, steer, stop, status` plus a capabilities set) with transports as drivers (process-headless, acp, rpc-jsonl, http-server, tmux, sdk); launch, observe, steer and cleanup declared in the descriptor; a missing capability is `unavailable`; reviewers of kind `command` become lanes of the same interface | DRV-lane-interface (over CP-backend-iface, AC-backend, launcher) | DECIDED |
| D-DRV-5 | Extension by user, project and company descriptor/model packs in the D-config-layer-order layers; keys that run a process (`[headless]` argv, `[probe]`) are operator-only (never from a cloned project); company packs are data only and digest-pinned (CM-packs pattern); `schema` version per file | DRV-packs | DECIDED |
| D-DRV-6 | CLI under existing nouns, no new MCP tool: `ddflow harness add|lint|test|probe|show|facts`, `ddflow model show|lint|probe|resolve` | DRV-cli | DECIDED |
| D-DRV-7 | Keep the schema a record: no conditionals, loops, expressions or includes; closed placeholder list; a dead-field lint; a field needs a reader and either two users or a safety reason | DRV-engines, DRV-conformance | DECIDED |
| D-DRV-8 | **Overridden by the operator**: option C now, that is descriptor-first, engines, catalog, Lane PLUS a plugin SDK for third-party drivers (entry points or external executables speaking a JSON protocol); recommended 'option B, C after two real requests' rejected | DRV-plugin-sdk (behind DRV-packs, DRV-lane-interface, DRV-conformance) | DECIDED |

## Questions for the operator (task DRV-decide), each with a default

- **DRV-Q1** Adopt option B (descriptor-first with named engines, model catalog, Lane interface) and defer C? Default: **yes**.
- **DRV-Q2** Model axis home: extend CC-inventory's `context_families.toml` into `ddflow/models/` with routes and pair tables, or keep model facts split by consumer? Default: **one catalog, extend CC-inventory**.
- **DRV-Q3** Fact status vocabulary: HX-facts-core says verified|documented|unverified|none; the research uses V/R/U and `clients.py` has `reported` and `observed`. Default: **verified, documented, reported, unverified, none; `observed` is a source, not a status**.
- **DRV-Q4** Rename the descriptor key `family` (a lineage label) to `lineage`, keeping `family` for the model's pretraining family used by review independence? Default: **yes, with a read alias until 1.0**.
- **DRV-Q5** May a project-layer descriptor add detection, paths and facts (never launch or probe argv)? Default: **yes, with the operator-only split**.
- **DRV-Q6** Mirror an external catalog (LiteLLM-shaped JSON) as an optional import for model windows and prices? Default: **optional, off; ddflow keeps only the fields it reads, each with source and date**.
- **DRV-Q7** Reviewer `PRESETS` move into the route/model data and reviewers run through the Lane interface? Default: **yes, after DRV-lane-interface; behaviour unchanged**.
- **DRV-Q8** Extend the no-agent-literals ratchet to substring matches as a second, report-only counter first? Default: **yes, report-only until DRV-migrate**.

## Alternatives rejected

- B then C after two real requests (the original recommendation, D-extension-seams): overruled by the operator, see D-DRV-Q1.
- A (status quo): about 70 open parity and ACP tasks would add agent-specific launch code in the places the 110 sites already show; duplicates logic.
- (Reversed by the operator) C now: it freezes a protocol before a second consumer exists; D-extension-seams said later. Adopted anyway, with the risks under 'Would invalidate'.
- One generic "driver per agent in Python": the descriptor already covers about 60 % of differences as data (FieldMap is the proof).
- Model facts inside harness descriptors: a model is served through several harnesses and routes.

## Would invalidate

Risks of the operator's override of option B: more moving parts early (a plugin protocol, trust labels and a sandbox before the descriptor and engines work has proved the schema), a protocol frozen with no second consumer, descriptor schema churn leaking into the SDK. Mitigation: DRV-plugin-sdk is small and sits last in wave order. Plus: More than a third of the next six added agents still need Python after the engines land; ACP becoming universal with usage and limit signals; facts rotting faster than they are verified; no third-party agents ever added; descriptor schema churn staying high after descriptor-v2 and `[tokens]`.

## Answers

| Q | Answer | Basis | Decision |
|---|---|---|---|
| DRV-Q1 | Option C with plugin SDK now, instead of B then C | operator, explicit | D-DRV-Q1 |
| DRV-Q2 | One model catalog extending CC-inventory | default, accepted with option C; operator may overrule | D-DRV-Q2 |
| DRV-Q3 | verified, documented, reported, unverified, none; observed is a source | default, accepted with option C; operator may overrule | D-DRV-Q3 |
| DRV-Q4 | Rename to lineage, read alias until 1.0 | default, accepted with option C; operator may overrule | D-DRV-Q4 |
| DRV-Q5 | Project layer adds detection, paths, facts, never launch or probe argv | default accepted | D-DRV-Q5 |
| DRV-Q6 | External catalog import optional and off | default accepted | D-DRV-Q6 |
| DRV-Q7 | PRESETS into model/route data; reviewers through Lane after DRV-lane-interface | default accepted | D-DRV-Q7 |
| DRV-Q8 | Substring counter, report-only until DRV-migrate | default accepted | D-DRV-Q8 |

Plugin SDK scope (task DRV-plugin-sdk): entry points or external executables speaking a JSON protocol; registration is operator-only; sandbox and trust labels; the conformance suite is reused for plugins; kept small. DRV-packs keeps its design note, now realised by DRV-plugin-sdk.
