# Managing project specifications: what ddflow has, what is planned, what is missing

Task `SPEC-research` (phase `P-specs`). Written 2026-10-21 by agent `res-specs`.

Operator question (verbatim, also recorded with `ddflow session prompt`): "Make a research of how
ddflow can manage project specifications, do a deep research and check what we have, what we plan,
what could be reused and make a proposition with phase and tasks with dependencies and waves for
the gaps"

## Evidence tags

| Tag | Meaning |
|---|---|
| CONFIRMED | read in the code or a doc at the cited place, or a command was run here and its output is quoted |
| REPORTED | taken from an earlier ddflow research note or a fetched page that this note did not re-verify |
| THEORETICAL | a design, a survey, or a source not opened |

Measurements are from this checkout on a loaded machine (a `docs check` over the repository took
more than 120 s, so no full test suite was run). Research notes: `R-specs-*` in the log.

## 1. The short answer

1. **ddflow manages specifications badly today, because it has no spec at all, only files.**
   A design or feature spec is a markdown file under `docs/ddflow/` that the log does not know:
   25 research notes and 37 decision documents are tracked by git, and tasks cite them only as
   prose ("Evidence: docs/ddflow/research/requirements-management.md", e.g. in `RQM-trace`).
   There is no spec record, no lifecycle, no digest, no owner, no scope. CONFIRMED (`ls
   docs/ddflow/research docs/ddflow/decisions`; `ddflow/core/records.py:268` `Item` has `needs`,
   `globs`, `body`, `tags` and no reference field; `ddflow docs --help` lists only `check`).
2. **The pieces around a spec are strong, and mostly planned elsewhere.** The record contract and
   data kinds (done), document types with validation and per-document guidance (done), the guidance
   engine (done), the plan checker and waves (done), the review machinery with the cross-family rule
   (done), the completion ledger with a content digest (done, for the task text only), and a
   planned family of traceability, baseline, adapter and import tasks (`PD-trace`, `PD-baseline`,
   `CN-spec-kind`, `CN-baseline`, `SDD-model`, `RQM-trace`, `TF-trace`). CONFIRMED by reading
   the tasks.
3. **The queue already holds three spec-shaped kinds that do not know each other**: `SDD-model`
   (a `spec-source` kind for Spec Kit / OpenSpec / Kiro trees), `CN-spec-kind` (a `spec` kind for
   OpenAPI / protobuf / ASN.1 across sister projects) and the inert shipped `requirement` kind.
   None is the native, in-project feature/design/interface/acceptance spec the operator is asking
   about. The proposal is ONE kind `spec`, of which the first two become instances (section 3).
4. **The gaps are binding and delivery, not storage**: (a) no spec record; (b) a task does not pin
   the spec it implements, so an edit after claim is invisible; (c) nothing reaches an agent from a
   spec (`inject.collect` returns rules and decisions only); (d) no spec-to-plan step that carries
   section ids and acceptance criteria into tasks; (e) no spec review and approval step; (f) no
   spec-versus-code drift or contract check; (g) no acceptance-criterion to test to commit trace.
5. **Proposal**: phase `P-specs`, 21 small tasks (one is this research), 9 in-phase waves, held
   behind the P-gaps core and the docs wave through `SPEC-decide` like `PI-decide`, no new MCP
   tool, one decision task with recommended defaults. Section 10.

## 2. What "specification" means for ddflow

A requirement says **what must hold** (one statement, one source, usually one verification). A
specification says **how a bounded piece of the system will be and behave** so that work can be
planned, built and judged against it. It is a document of many sections, owned, reviewed, versioned,
and cited by the work that implements it. Overlap exists only where a spec section restates or
refines requirements; then it links, not copies.

| Class | Question it answers | Typical content | Machine check | Where ddflow stands |
|---|---|---|---|---|
| feature spec | what will this feature do, for whom, how do we know it is done | purpose, scope, behaviour, acceptance criteria, open questions | acceptance criteria run as tests | no record; Spec Kit/Kiro/OpenSpec files only via planned P-sdd |
| design doc / technical spec | how will it be built, and why this way | components, data model, trade-offs, rejected options | file/pattern/config assertions on the structure | `docs/ddflow/research/*.md` and `docs/ddflow/decisions/*.md` as loose files |
| interface contract | what exactly does a boundary promise | CLI/MCP tool surface, config keys, event payloads, OpenAPI, protobuf, JSON Schema | a diff/lint/contract tool (oasdiff, buf, Schemathesis, Pact) | ddflow's own: declared surfaces + golden snapshots + compat tests (executable, not a spec record); foreign formats: planned `CN-adapter-*` |
| acceptance suite / test plan | which scenarios prove it | Given/When/Then, EARS lines, test ids | the test run itself | tests are the de-facto spec; `SX-acceptance-field`, `TF-trace` planned |
| architecture decision | which choice was made and why | context, decision, consequences | fitness functions | decisions (done) and ADR doc type (done); `B-dec-*` planned |
| spec-as-source (SDD) | the spec generates or governs code | spec tree + task list | regeneration diff | P-sdd imports/exports; ddflow stays the executor (D-sdd-plan-modes) |

Not specifications here: engineering standards ("use CMake >= 3.25", `P-requirements`), product
requirements with stakeholders and EARS ("PD-req-kind", `P-product-docs`), shared specs between
sister projects (`P-constellation`). Decisions are ADRs and stay decisions.

## 3. The specification record model (proposed)

**One kind `spec`, a data kind** (`ddflow/templates/kinds/spec.toml`), prose in a file. Same
reasoning as D-requirements-model: a second engine means a second verb set to keep in step.

| Field | Meaning | Note |
|---|---|---|
| id, title | stable id (`SPEC-...` or the project's id scheme) and name | |
| class | `feature` / `design` / `interface` / `acceptance` | `interface` may carry `format` |
| state | `draft` -> `in_review` -> `approved` -> `implemented` -> `verified`; also `superseded`, `withdrawn` | `implemented` and `verified` are COMPUTED from linked tasks and checks, not set by hand |
| owner, review_by | person or role; review date | reuse the B-dec-review reminder idea |
| version | monotonic revision plus the digest at each transition; named sets are baselines | baselines are the PD-baseline / CN-baseline engine |
| globs, contexts | scope: which files/areas the spec governs | same matcher as `rule`/`decision`/`RQM-scope` |
| file | path of the prose (`docs/specs/<slug>.md`, document type `spec`) | the file is the text, the record is the index, lifecycle and digest |
| digest | content digest of the canonical section text at the last transition | ignores generated regions and trailing whitespace |
| sections | stable ids (`#ac-3`) derived from headings | the unit of citation, injection and trace |
| requirements[] | links to `requirement` records | link, never copy |
| tasks, phases, tests, commits | trace edges | derived from `SPEC:` lines, test markers, ledger shas (SPEC-trace), not typed by hand |
| source | `local` / `remote` / `generated` / `imported` with uri and rev | imported text is trust=imported and fenced |
| format | `markdown` / `openapi` / `proto` / `jsonschema` / ... | where `CN-spec-kind` and `SDD-model` attach |
| checks[] | machine checks per section: `command`, `pattern`, `file`, `config`, `surface` | run by the shared verify runner |

**One new kind vs reuse.**
- Reuse: the record contract RC1-RC12 and data kinds (`SE-kind-data`), the doc-type engine for the
  file, `requirement` for statements, `decision` for ADRs, `lesson` for what was learned, tasks for
  the work, research claims for a falsifiable spec section.
- New: `spec` (one kind). `SDD-model`'s `spec-source` and `CN-spec-kind` should be instances (`source=imported`,
  `format=openapi`) instead of two more kinds. This contradicts neither task's design intent; it
  needs the operator's yes (SPEC-decide) and a queue edit (SPEC-reconcile).
- Not new: no `design` kind, no `acceptance` kind (a class value), no `contract` kind (the
  existing `contract` kind is a vendor contract, `ddflow/templates/kinds/contract.toml`).

## 4. Inventory: what ddflow has today

### 4.1 Capability matrix

Columns are the places a spec lives today. Y = CONFIRMED present, P = partial, N = CONFIRMED absent,
plan = only in the queue (owner id).

| Capability | Loose doc file (`docs/ddflow/**`) | Decision record | `requirement` kind (inert) | Task body / `ACCEPTANCE:` | Declared surfaces + goldens + compat | Spec as a kind (proposed) |
|---|---|---|---|---|---|---|
| store | Y git file, outside the log | Y log | Y log def | Y log | Y code + 5 `.ambr` files, 553 KB | plan SPEC-model |
| list / show | N (`ddflow docs` has only `check`) | Y list, show, applicable | Y `record requirement list` | Y | P `help`, `export` | plan SPEC-surfaces |
| add / edit / remove | hand edit | add Y; edit N; remove N (R-requirements) | Y generic | Y | code edit | plan SPEC-surfaces |
| supersede | N | Y (`api/decisions.py`) | Y generic record verbs | N | n/a | plan SPEC-surfaces |
| duplicate / conflict check | N | Y | Y generic add-time | P task add `--check` | n/a | plan SPEC-dupes |
| scope by glob / context | P doctype glob only | Y `Governs` globs | N | Y task globs | Y per command | plan SPEC-model |
| review workflow | P code review of the diff when the file changes | N | N | Y gates | Y compat classification | plan SPEC-review |
| approval | N | Y operator decides | P lifecycle only | n/a | n/a | plan SPEC-review |
| versioning / baselines | git only | supersede chain | N | N (ledger digest of task text only) | P golden snapshot is a baseline | plan SPEC-baseline (PD-baseline) |
| trace to tasks / tests / commits | N (prose "Evidence:" lines) | P link verbs are for duplicates only | plan RQM-trace | P ledger lists files changed and test files | P tests ARE the contract | plan SPEC-trace (RQM-trace, PD-trace, TF-trace) |
| drift: spec vs code | P README only: `docs check` compares README claims with `--help`/tool/knob extractors (ran here: 13 flag findings in the first 15 lines) | N | N | P ledger: task text edited after completion is flagged (`ledger.py:125`) | Y golden + `test_compat*` (18 files) | plan SPEC-bind, SPEC-contract |
| injection into sessions | N | Y claim and brief show applicable decisions (CONFIRMED in this claim's guidance block) | N (`inject.collect` knows rules and decisions only, `inject.py:104-114`) | Y it is the task | n/a | plan SPEC-inject (RQM-provider) |
| enforcement / check | P `docs check` | P warn by default | N | Y gate pipeline | Y tests fail the build | plan SPEC-gate, SPEC-check |
| import / export | `export` of generated views | ADR dirs import, export | N | `plan apply` files tasks (done) | `export` | plan SDD-*, CN-adapters |
| MCP / CLI parity | n/a | P (no search tool) | CLI only via `record` | Y | Y parity test | plan SPEC-surfaces (no new tool) |
| tests | `docs check` tests | Y | Y kind tests | Y | Y | plan every task |

Facts behind the cells (CONFIRMED unless tagged):
- Doc types shipped: `adr, bugs, changelog, decisions, roadmap, rules, runbook, sessions, status,
  worklog` (10, `ls ddflow/templates/doctypes`). No `spec`, `design` or `feature` type. Kinds
  shipped: 13 (`ls ddflow/templates/kinds`), none a spec.
- `requirement.toml` has lifecycle `proposed -> approved -> implemented -> verified`, no scope, no
  digest, no verify. It is a good LIFECYCLE template for the spec kind.
- The plan machinery: `ddflow plan check|apply|confirm`; `services/plancheck.py` reports
  `wide_glob, unknown_dep, cycle, overlap, no_acceptance, wide_span, duplicate` and the waves from
  `core.schedule.waves`; `plan_apply.body_of` carries an `acceptance` field into an `ACCEPTANCE:`
  line. Checked here: a plan naming `B-no-such-item-xyz` yields `unknown_dep`, a plan naming real
  queue ids yields none. So a plan CAN cite queue ids; it cannot cite a spec section.
- Declared surfaces: 157 `Command(` declaration sites in `ddflow/surfaces/declared/*.py`
  (7,816 lines); golden snapshots `tests/golden/__snapshots__/` 553 KB (MCP tools 153 KB, CLI help
  116 KB); `tests/test_compat*.py` 18 files; `docs/ddflow/compatibility.md` lists nine interface
  kinds and classifies a change additive, compatible or breaking. This is an executable interface
  spec with a contract test; what it lacks is a spec RECORD that names it.
- Ledger: `services/ledger.py` stores `requirement_digest(title, body, globs)` at completion and
  reports the task text as `drifted` if edited later. The same mechanism does not exist for any
  document a task cites.
- Dedupe: `ddflow similar|dupes|link` handle near-duplicate pairs of tasks, bugs, lessons,
  decisions and rules, with the relation answers `new|extends|duplicate_of|related|distinct`.
- Task bodies as specs: the queue itself is the best spec corpus ddflow has (task bodies carry
  `ACCEPTANCE:`), but a body is one task's text, not a document with sections and an approver.

### 4.2 Defects and limits met while measuring

See section 11.

## 5. What is planned (ids, states, deps)

All open at the time of writing unless marked done. "Overlap" says what part of the spec problem
the item already covers.

| Id | State | Needs (short) | Overlap |
|---|---|---|---|
| `P-requirements` `RQM-*` (27 tasks) | open, `RQM-research`, `RQM-decide` done | `RQM-model` ... | record scope/strength/verify/inject machinery that SPEC reuses: `RQM-provider` (adapter seam), `RQM-check` (verify runner), `RQM-trace` (suspect links), `RQM-gate`, `RQM-dupes`, `RQM-inject`, `RQM-sources` |
| `P-product-docs` `PD-req-kind`, `PD-trace`, `PD-baseline`, `PD-lint`, `PD-doc-types` | open | `PD-req-kind` -> `PD-trace` -> `PD-baseline` | product requirements, suspect links by parent hash, baselines, EARS lint, PRD/epic/story types. Not design/interface specs. |
| `P-sdd` `SDD-model` ... `SDD-trace` | open | `SDD-probe` -> `SDD-model` ... | import/export of Spec Kit/OpenSpec/Kiro trees; `SDD-model` owns a `spec-source` kind; `SDD-doctypes` ships `sdd-spec`, `sdd-plan`; `SDD-trace` is the `/analyze` equivalent |
| `P-constellation` `CN-spec-kind`, `CN-baseline`, `CN-digest`, `CN-breaking-gate`, `CN-adapters-core`, `CN-adapter-*`, `CN-spec-fence`, `CN-drift-audit`, `CN-trace`, `CN-req-import` | open, `CN-decide` abandoned (decision `D-constellation` recorded) | `CN-manifest` ... | format specs between projects: kind, immutable versions, model+content digest, baselines, breaking-change gate (warn), drift audit, import of Doorstop/StrictDoc/sphinx-needs/ReqIF |
| `P-docs-generic` `B-dt-obligations`, `B-dt-health`, `B-dt-drafts`, `B-dt-workflow`, `B-dt-library`, `B-dt-onboard` | open (the `B-dt-spec`, `-validate`, `-sources`, `-rules` are done) | | when a document is due (depends_on globs), document health (stale by co-changed code), drafts with approval, a document task with its own pipeline, shipped types |
| `P-decisions` `B-dec-checks`, `B-dec-adr-files`, `B-dec-review`, `B-dec-waivers` | open | | executable decisions (fitness functions), MADR files, review dates, waivers |
| `P-record-contract` `B-rc-changes`, `B-rc-context`, `B-rc-conformance` | open (`-lifecycle`, `-participation` done) | | change feed ("changed since you last looked"), relevance-matched context for every kind |
| `P-test-flows` `TF-trace`, `TF-record`; `P-research` `B-rw-claims`, `B-rw-checks` | open | | claim -> test -> result traceability; executable checks for claims |
| `SX-acceptance-field` | open | | structured acceptance and test-oracle fields on tasks with a ratchet |
| `P-plan-modes` `PL-*` | open | | plan text to checkable plan, proposed intake, exit hook; the plan side of spec-to-plan |
| `DX-drift-notice`, `DX-merge-fanout-notify` | open | | tell a running agent its base moved (files); the sibling for spec changes |
| `SU-digest` | open | | interface digest and soft/hard classifier of ddflow's own MCP surface (a digest of the declared interface) |
| `P-goals`, `P-org-config`, `P-lean-start` `LS-*`, `D-requirements-injection` | open / decision | | strength model, org layering, token budget of session start (32K) |

Duplicates avoided on purpose (checked with `ddflow similar`, which reported `CN-baseline`,
`SDD-model`, `B-dt-health`, `RF-docs`, `SE-eval-harness` as "reads like"; each was read and is
related, none is the same scope):
SPEC does not add a baseline engine, a suspect-link engine, a verify runner, a lint engine, a
guidance adapter, an adapter registry, a drift notice channel, a doc-type engine or an import
parser.

## 6. What can be reused (confirmed)

| Piece | Where | Used by SPEC for |
|---|---|---|
| Record contract RC1-RC12, data kinds, kinds registry | `SE-kind-data`, `B-rc-lifecycle`, `B-rc-participation` (done) | the `spec` kind: history, supersede, merge, search, recall, resource, RAG export for free |
| Guidance engine (scope, rank, budget, provenance fence, dedupe) | `ddflow/services/guidance/` | injection of sections; `RQM-provider` is the seam it lacks |
| Doc-type engine, validate, sources, guidance per document | `B-dt-spec`, `-validate`, `-sources`, `-rules` (done) | the `spec` file type, section anchors, generated index, guidance by document |
| Plan checker and waves | `services/plancheck.py`, `plan_apply.py`, `core.schedule.waves` (done) | spec -> plan; this very proposal was checked with it |
| Review gates, independence rule, triage | `services/review.py`, `D-review-triage` | cross-family spec review as a gate |
| Completion ledger digest | `services/ledger.py` | digest at claim and completion for cited specs |
| Suspect-link primitive | `RQM-trace` / `PD-trace` / `TF-trace` (all open: one mechanism) | spec edit marks dependent completed tasks suspect |
| Baseline engine | `PD-baseline` / `CN-baseline` | spec baselines and diffs |
| Verify runner and checks engine | `RQM-check`, `services/guidance/checks.py` (built, unwired), `services/cmdrunner.py` | spec checks; unavailable is never passed |
| Docs check surface extractors | `services/docscheck_extract.py`, `[[docs.extract]]` | code side of an interface contract check |
| Similar/dupes | `services/similar.py` | add-time duplicate and conflict check |
| Change feed, drift notice | `B-rc-changes`, `DX-drift-notice` | notify items that cite a changed spec |
| Research notes as claim + probe | `ddflow research add` | a spec section that is a falsifiable claim gets a probe in its check |
| Hazard scan, fence | `SE-hazard-scan`, `CN-spec-fence` | imported spec text |
| Lean-start budget | `docs/ddflow/research/lean-start.md`, `LS-charter` | no spec in the always-on charter; sections only on demand |

## 7. Other systems (only what changes the design)

REPORTED rows come from `docs/ddflow/research/sdd.md` (2026-10-16, pages fetched there).
FETCHED rows were opened for this note on 2026-10-21; fetched content was treated as data.

| System | What it does | What it changes in the design | Tag |
|---|---|---|---|
| Kiro specs | `.kiro/specs/<name>/{requirements.md, design.md, tasks.md}`, EARS requirements | one spec has three views (what, how, work); ddflow keeps what+how as sections of ONE file and gets the work view from `spec plan` | REPORTED |
| GitHub Spec Kit | `spec.md` with `[NEEDS CLARIFICATION]`, `plan.md`, `tasks.md`, constitution | clarification markers become ask placeholders that block `approved`; the constitution is rules, not a spec | REPORTED |
| OpenSpec | `specs/` = current behaviour, `changes/<c>/` with ADDED/MODIFIED/REMOVED delta specs, archive | store no delta files: a baseline diff by section digest yields the same ADDED/MODIFIED/REMOVED | REPORTED |
| Cursor / Claude Code plan modes | plan before edit | already covered by `P-plan-modes`; the spec is the input to the plan | REPORTED |
| Schemathesis | property-based tests generated from OpenAPI 2.0-3.2 / GraphQL; checks server errors, schema violations, input-validation gaps; JUnit XML / HAR / Allure output | a contract check is a `command` check whose evidence is JUnit; feed `TF-record` | FETCHED |
| Pact (consumer-driven contracts, can-i-deploy) | provider/consumer verification matrix | cross-project, already planned as `CN-matrix`; not needed in-project | REPORTED (R-constellation) |
| sphinx-needs | "define, link, and analyse" features, requirements, specifications and test cases; import/export to Jira, GitHub, spreadsheets | typed items with typed links is what the record contract gives; import is `CN-req-import` | FETCHED (index page only; link types and warnings not read) |
| Doorstop | each item a YAML file in a document directory; documents form a tree; `doorstop link CHILD PARENT`; integrity checks | file-per-item with parent links confirms the file + index design; the suspect-link semantics (reviewed stamp, link stamps) were NOT on the pages fetched | FETCHED; suspect semantics THEORETICAL |
| StrictDoc | "software for technical documentation and requirements management" (.sdoc) | only the index page was readable: nothing about its relations or status model is claimed here | FETCHED (index only) |
| ADR / MADR / arc42 | one decision per record; sections by concern | ADRs stay decisions (`B-dec-adr-files`); arc42-style section lists belong in the `design` doc type's sections | THEORETICAL |
| Gherkin / BDD | Given/When/Then scenarios as the spec | acceptance criteria written as scenarios are testable by `P-test-domains`; SPEC-lint accepts GWT or EARS as "observable" | THEORETICAL |
| TLA+ / Alloy | formal models | out of scope; a spec `check` of kind `command` can wrap a model checker, nothing more | THEORETICAL |

## 8. Gaps, ranked

| # | Gap | Evidence | Task |
|---|---|---|---|
| 1 | No spec record: specs are loose files with no lifecycle, owner, scope or digest | doc types list; `Item` fields; 62 research/decision files untracked by the log | SPEC-model, SPEC-surfaces, SPEC-docs-sync |
| 2 | A task does not pin the spec it implements; an edit after claim is invisible | `ledger.py` digests the task text only | SPEC-bind, SPEC-notify |
| 3 | Nothing from a spec reaches an agent | `inject.collect` = rules + decisions | SPEC-inject (needs RQM-provider) |
| 4 | No spec-to-plan carrying section ids and criteria | `plan_apply.body_of` knows `ACCEPTANCE:` only | SPEC-plan |
| 5 | No spec review/approval step with an independent reviewer | approval exists only for decisions and gates on diffs | SPEC-review |
| 6 | No acceptance criterion -> task -> test -> commit trace | `ddflow link` only relates duplicates; `TF-trace` and `RQM-trace` are open | SPEC-trace |
| 7 | No spec-vs-code drift or interface-contract check | `docs check` covers README claims only; goldens pin ddflow's own surface but are not tied to a spec | SPEC-check, SPEC-contract, SPEC-gate |
| 8 | Three spec-shaped kinds in the queue that do not know each other | `SDD-model`, `CN-spec-kind`, `requirement` | SPEC-decide, SPEC-reconcile |
| 9 | No add-time duplicate/conflict check between specs, requirements, decisions | `similar` covers other kinds | SPEC-dupes |
| 10 | No management view (approved but unimplemented, in scope but uncited, stale) | none | SPEC-report |
| 11 | No baseline/diff for specs | `PD-baseline` open | SPEC-baseline |
| 12 | No spec lint (structure, testable criteria, dangling references) | `PD-lint` covers requirements only | SPEC-lint |

Not gaps of this phase (already owned): import of Spec Kit/OpenSpec/Kiro (`P-sdd`), external format
adapters and cross-project propagation (`P-constellation`), doc-type machinery (`P-docs-generic`),
requirement scoping, strength and layers (`P-requirements`).

## 9. Design

### 9.1 Workflow

```
draft (SPEC-docs-sync file + record) -> lint (SPEC-lint) -> in_review
  -> cross-family review gate (SPEC-review) -> approved (event: approver, digest)
  -> spec plan (SPEC-plan) -> plan check -> plan apply (PROPOSED) -> operator confirm
  -> tasks cite  SPEC: <id>@<digest>#<section>
  -> claim pins the digest (SPEC-bind), injects the section (SPEC-inject)
  -> implement; spec edited meanwhile -> notify (SPEC-notify)
  -> checks run (SPEC-check/SPEC-contract); specs gate (SPEC-gate)
  -> complete stores spec digests in the ledger; all tasks done -> implemented; checks pass -> verified
  -> spec revised later -> dependents suspect (SPEC-trace) -> new baseline (SPEC-baseline)
```

Execution stays in ddflow's claim/gate/merge loop and `plan apply` files tasks PROPOSED
(`D-sdd-plan-modes`); the spec never confirms its own plan.

### 9.2 Traceability both ways

Edges are derived, not typed: task -> spec from the `SPEC:` line (a grammar parsed once and
shared); test -> spec from a marker `# spec: ID#section` or a test id in a check
(`services/testidents.py`); commit -> spec from the ledger's landing sha. `spec trace ID` lists
each criterion with its tasks, tests and commits; no edge = `uncovered`. Suspect marking reuses the
parent-hash mechanism of `RQM-trace`/`PD-trace`/`TF-trace`; this note recommends deciding in
`SPEC-reconcile` that those three plus `SDD-trace` and `CN-trace` are ONE mechanism with typed edges.
LLM-proposed links stay unconfirmed until a person or a deterministic check confirms them (`PD-trace`).

### 9.3 Drift and staleness

Three kinds, three detectors, all warn by default (`D-constellation` strictness reading, a knob to
block):
1. **Spec changed after claim** (digest at claim differs): shown at heartbeat, brief and completion
   (`SPEC-bind`, `SPEC-notify` via `B-rc-changes` and `DX-drift-notice`, no new push channel).
2. **Code changed under an approved spec** without the spec changing: the co-change rule of
   `B-dt-health` (commits touching the spec scope since it last changed) in `SPEC-report`.
3. **Spec claims something the code does not**: `SPEC-check` (patterns, files, config, commands)
   and `SPEC-contract` (names in an interface spec versus the declared surfaces: CLI flags, MCP
   tools, config keys, using the existing docscheck extractors).

### 9.4 Contract checks

For ddflow's own interface the code side already exists and is exact: the declared surfaces, the
registry and the goldens. `SPEC-contract` reads those, not the prose, and classifies a difference
with the compat page's additive/compatible/breaking. Foreign formats go through the `CN-adapters-core` seam when it lands; until
then a `command` check wraps the project's own tool (`oasdiff`, `buf`, Schemathesis) and a missing
tool is recorded `unavailable`, never passed (rule 1 of this repository).

### 9.5 Injection tiers and budget

| Tier | What | Budget | Note |
|---|---|---|---|
| always-on charter | none for specs; at most a one-line index "N approved specs, `ddflow spec for <path>`" | inside the 500-token requirements charter, never extra | `D-requirements-injection`, lean-start |
| contextual | the SECTIONS (not the file) of approved specs the task cites or whose scope its globs touch | `[specs].claim_tokens`, recommended 600 | at claim and pre-edit; ranked and fenced by the guidance engine |
| on demand | `spec show --section`, `spec search`, `spec for` | caller's | MCP resource and the record tool |
| change | a notice that a pinned spec changed | one line | via the change feed |

Imported or remote spec text is `trust=imported`, hazard-scanned and fenced; spec text never carries
a tool grant or a command (`CN-spec-fence` rule).

### 9.6 Conflict and duplicate detection

At add time and on a scheduled pass: same scope + similar title among approved specs of one class,
a spec statement that contradicts a requirement or decision on the same topic (reuse the cross-kind
check and `RQM-dupes`'s comparison), overlapping scope globs. The answer protocol is the one task
add already uses (`new | extends | duplicate_of | related`).

### 9.7 Layering

Reuse `D-requirements-layers`: org > team > project > local > task, tighten-only. For specs the
practical layers are two: a project spec, and an org-provided template or interface standard
(delivered as a requirement, class `standard`, or a doc-type override through `P-org-config`). A
task-local spec is a section of the task body. No spec layering engine is proposed.

### 9.8 Import and export

Import of external trees and formats is not this phase's: `SDD-import` produces tasks and
`spec-source` records, `CN-adapters-core` produces `format` specs. This phase makes them instances
of ONE kind (`SPEC-reconcile`) and adds the native export: the generated `SPECS.md` index and a
section diff between baselines. Two-way sync stays deferred (`D-sdd-plan-modes`).

## 10. Phase and tasks

Phase `P-specs`, 21 tasks (`SPEC-research` is this note), none beyond it started. Phase `needs`:
`SE-kind-data`, `B-dt-rules`, `B-rc-participation` (all done), the same as `P-requirements`.
**The hold of `D-old-work-behind-core-docs-accepted`** is carried by `SPEC-decide`, exactly as
`PI-decide` does, with the seven ids `B-gap-log-scale`, `B-gap-lanes`, `B-gap-plan-lint`,
`P-doc-templates`, `P-docs-generic`, `P-roadmap`, `P-docs-search`. Every task is a literal-file
glob set (new directories `ddflow/services/specs/`, `tests/test_specs_*.py`).

| Task | Needs | Kind | Scope |
|---|---|---|---|
| SPEC-research | - | task | this document and the `R-specs-*` notes |
| SPEC-decide | SPEC-research + the 7 hold ids | doc | operator decisions: one kind, lifecycle, baseline, approver, injection budget, drift policy |
| SPEC-model | SPEC-decide | task | `spec.toml` kind, lifecycle, fields |
| SPEC-surfaces | SPEC-model | task | `spec list/show/add/edit/remove/supersede/search/for` on CLI and MCP (no new tool); `[specs]` config |
| SPEC-docs-sync | SPEC-model | task | doc type `spec`, file <-> record, digest, section anchors, generated index |
| SPEC-dupes | SPEC-model | task | duplicate/conflict check against specs, requirements, decisions |
| SPEC-reconcile | SPEC-decide, SPEC-model | doc | make `SDD-model` and `CN-spec-kind` instances; relink the trace family |
| SPEC-lint | SPEC-docs-sync | task | structure, testable criteria, TBD, dangling refs as data |
| SPEC-plan | SPEC-surfaces, SPEC-docs-sync | task | spec -> checkable plan with section citations and acceptance |
| SPEC-bind | SPEC-surfaces | task | `SPEC:` grammar, digest at claim and completion, ledger fact |
| SPEC-check | SPEC-surfaces, RQM-check | task | per-section machine checks through the shared runner |
| SPEC-review | SPEC-lint, B-dt-workflow | task | cross-family review gate and approval event |
| SPEC-inject | SPEC-bind, RQM-provider | task | section injection under `claim_tokens` |
| SPEC-contract | SPEC-check | task | interface spec vs declared CLI/MCP/config surfaces |
| SPEC-notify | SPEC-bind, B-rc-changes, DX-drift-notice | task | notify items that cite a changed spec |
| SPEC-trace | SPEC-bind, RQM-trace | task | criterion <-> task <-> test <-> commit; suspect marking |
| SPEC-gate | SPEC-check, SPEC-bind, SPEC-review, B-pipe-model, B-pipe-resolve | task | `specs` gate by scope; computed `implemented`/`verified` |
| SPEC-baseline | SPEC-trace, PD-baseline | task | baselines and sectionwise diff |
| SPEC-report | SPEC-trace, SPEC-notify, SPEC-dupes | task | doctor, brief line, metrics |
| SPEC-e2e | SPEC-gate, SPEC-plan, SPEC-inject, SPEC-notify, SPEC-contract, SPEC-report, SPEC-baseline, SPEC-reconcile | task | transcript test with seeded faults |
| SPEC-docs | SPEC-e2e | doc | help topic, workflow page, driver text, example spec pack |

### Waves

Verified by `verify_waves.py` over the folded queue (every `needs` exists in the queue, no cycles in
the transitive closure of 47 reachable nodes, no glob collision between tasks that can run together).

In-phase waves (every need outside the phase counted as done):

| Wave | Tasks (run in parallel, disjoint globs) |
|---|---|
| 1 | SPEC-research |
| 2 | SPEC-decide (carries the hold) |
| 3 | SPEC-model |
| 4 | SPEC-surfaces, SPEC-docs-sync, SPEC-dupes, SPEC-reconcile |
| 5 | SPEC-bind, SPEC-check, SPEC-lint, SPEC-plan |
| 6 | SPEC-inject, SPEC-notify, SPEC-trace, SPEC-contract, SPEC-review |
| 7 | SPEC-gate, SPEC-baseline, SPEC-report |
| 8 | SPEC-e2e |
| 9 | SPEC-docs |

Open outside chains lengthen three tasks and what follows them; every other task keeps its
in-phase wave. Counting each open outside item as one step per level of its own needs (a lower
bound, because an open phase is counted as one step): `SPEC-gate` is at least wave 8 (it waits on
the `B-pipe-*` chain), `SPEC-baseline` at least wave 11 (it waits on `PD-req-kind` -> `PD-trace` ->
`PD-baseline`), so `SPEC-e2e` is at least wave 12 and `SPEC-docs` at least wave 13. `SPEC-decide`
itself waits on the four open hold phases (below), which delays the whole phase but not the
numbering. Consequence: the first usable slice is waves 1-5 (record, surfaces,
file sync, binding, checks, lint, plan); `SPEC-gate` and `SPEC-baseline` arrive with the pipeline and
product-docs work, and `SPEC-e2e` should not be started until they do. If the operator wants the
slice earlier, `SPEC-e2e` can be split into a slice test (waves 1-6) and a full test; that choice is
a `SPEC-decide` question.

`SPEC-decide` waits on four OPEN phases (`P-doc-templates`, `P-docs-generic`, `P-roadmap`,
`P-docs-search`) by design (`D-old-work-behind-core-docs-accepted`); lift the hold with
`ddflow update SPEC-decide --needs ...` only if the operator says so.

Linked (not re-added): `SPEC-model` extends `SDD-model` and `CN-spec-kind`; `SPEC-surfaces`,
`SPEC-dupes`, `SPEC-inject`, `SPEC-check`, `SPEC-trace`, `SPEC-gate` relate to their `RQM-*`
counterparts; `SPEC-trace` to `PD-trace`, `TF-trace`, `SDD-trace`, `CN-trace`; `SPEC-baseline` to
`PD-baseline`, `CN-baseline`; `SPEC-review` to `B-dt-drafts`; `SPEC-report` to `B-dt-health`;
`SPEC-notify` to `DX-merge-fanout-notify`; `SPEC-plan` to `PL-slice`, `PL-plan-text`; the phase to
`P-requirements`, `P-sdd`, `P-constellation`, `P-product-docs`, `P-docs-generic`.

## 11. Defects filed while doing this research

| Id | What |
|---|---|
| `B-plan-check-new-file-warnings-noise` (fix task `fix-B-plan-check-new-file-warnings-noise`, low) | `plan check` printed 22 "does not exist" warnings for a plan whose tasks create the files, with unrelated "nearest" suggestions |
| lesson `L9d728061e0` | the scratchpad directory is shared between agents: another agent overwrote my `plan.json` |

Seen and already filed: `Bf4c3b12455` (`list --limit` cap, fixed: paging by `--offset` and the
`next` field worked here). Observed, not filed because it is the machine and not ddflow: every Bash
call prints `exa: command not found`; `ddflow docs check` took more than 120 s on the loaded machine.

## 12. UNKNOWN

- Doorstop's suspect-link and review semantics, StrictDoc's relation and status model and
  sphinx-needs' link types: the pages fetched were indexes. The design relies only on the primitive
  already planned in `PD-trace`.
- Whether `RQM-trace`, `PD-trace` and `TF-trace` will land as one primitive; `SPEC-trace` is written
  against `RQM-trace` and says so.
- Whether the MCP tool budget (about 45 bytes of headroom, `SDD-model` note) is still true; `SPEC-surfaces`
  assumes it and adds no tool.
- The token cost of a realistic spec section at claim: `[specs].claim_tokens` 600 is a guess to be
  measured in `SPEC-e2e` against the lean-start budget.
- How often agents will write specs at all. The recommended default is that an agent may DRAFT and
  never approve a feature, design or acceptance spec.
- Cost of `ddflow docs check` here: more than 120 s on this loaded machine; not measured further.
