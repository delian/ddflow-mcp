# ddflow and spec-driven development (SDD): integration research

Date 2026-10-16. Read-only research. V = verified from a fetched page; I = inference; U = could not verify. Tools move weekly: treat every version and star count as a snapshot.

## 1. What the tools are (verified facts)

**GitHub Spec Kit** ([repo](https://github.com/github/spec-kit), [docs](https://github.github.com/spec-kit/), [releases](https://github.com/github/spec-kit/releases)). V: MIT; 141.6k stars; latest v1.2.1 (Oct 16 2026), v1.2.0 Oct 13, v1.1.0 Oct 2: several releases a week. `specify` CLI (Python 3.11+, uv). Core flow Specify, Plan, Tasks, Implement, Converge; commands are now invoked as skills `/speckit-constitution|specify|plan|tasks|implement|converge`; extra commands (clarify, analyze, checklist) exist per older docs; the core `taskstoissues` command was deprecated in v1.1.0 and moved to a bundled GitHub extension. Layout: `.specify/memory/constitution.md`; `specs/001-*/{spec.md, plan.md, tasks.md, research.md, data-model.md, contracts/, quickstart.md}`. tasks.md line format from the shipped template: `[ID] [P?] [Story] Description` with T001-style IDs, `[P]` = parallelisable without file conflict, `[US1]` story tag, exact file paths in the text, phases Setup, Foundational, User Stories by priority, Polish, then a prose Dependencies section; checkboxes `- [ ]`. `[NEEDS CLARIFICATION]` marks open questions. 38 integrations (Copilot default, Claude, Gemini, Codex, Kilo, Zed, Kiro, generic fallback); `specify init` writes per-agent command/skill files (e.g. `.claude/commands/`). Extension system (Feb 2026): `extension.yml` (schema_version, id, version, requires.speckit_version, provides.commands/templates/scripts), commands named `speckit.{ext}.{cmd}`, hooks at `after_plan`, `after_tasks`, `after_implement` etc. with `optional`, `prompt`, `priority`; config in `.specify/extensions/{id}/`. Also presets (template overrides), workflows, bundles, self-hosted catalogs; 157 community extensions and 33 presets per the docs home page (105/22 in May 2026). Execution: the user's own coding agent runs `/implement`; Spec Kit has no scheduler or queue (I, from absence in docs; U: whether `workflows` add one).

**AWS Kiro** ([specs](https://kiro.dev/docs/specs/), [steering](https://kiro.dev/docs/steering/), [hooks](https://kiro.dev/docs/hooks/)). V: `.kiro/specs/<name>/{requirements.md, design.md, tasks.md}`; bugfix specs use `bugfix.md`; requirements use EARS ("WHEN ... THE SYSTEM SHALL ...") ([search summary](https://kiro.dev/docs/specs/concepts)); tasks.md is a markdown checkbox list; Kiro runs independent tasks concurrently in "waves"; IDE, CLI, web. Steering: `.kiro/steering/*.md` (workspace) and `~/.kiro/steering` (global), YAML front matter inclusion modes always/fileMatch/manual/auto, foundation files product.md/tech.md/structure.md, reads AGENTS.md. Hooks: JSON in `.kiro/hooks/`, event triggers with matcher, action = shell command or injected prompt; can gate tool execution. U: licence (proprietary product, I), exact tasks.md sub-markers (optional-task `*`, `_Requirements:_` refs are from memory and third-party skill pages, not the official page, whose concepts URL returned 404 for me).

**OpenSpec** ([repo](https://github.com/Fission-AI/OpenSpec), [concepts](https://github.com/Fission-AI/OpenSpec/blob/main/docs/concepts.md)). V: MIT; 72.2k stars; Node 20.19+; `openspec/specs/<domain>/` = current behaviour (source of truth); `openspec/changes/<change>/{proposal.md, specs/ (delta: ADDED/MODIFIED/REMOVED Requirements with scenarios), design.md, tasks.md}`; tasks.md = hierarchical numbered checklist; archive merges deltas into specs and moves the change to `changes/archive/<date>-...`; commands `/opsx:explore|propose|apply|archive`; 30+ agents via slash-command adapters; schemas define artifact dependency graph; beta "stores" for cross-repo specs. U: release cadence, CLI `--json` options.

**BMAD-METHOD** ([repo](https://github.com/bmad-code-org/BMAD-METHOD)). V: MIT (trademarked), 54.2k stars, stable v6.12 (`npx bmad-method install`), v7 alpha as skills/plugins for Claude Code and Codex. Artifacts: PRD, architecture, epics and stories, `_bmad-output/`, `sprint-status.yaml`; persona agents (PM, architect, dev, QA). Methodology-heavy, not a queue.

**Taskmaster** ([claude-task-master](https://github.com/eyaltoledano/claude-task-master)). V: 28.2k stars; MIT with Commons Clause (no resale/hosting); `.taskmaster/tasks/tasks.json` with id, title, dependencies, priority, status, subtasks, testStrategy; PRD parse; 36 MCP tools with tool-loading modes. It is the closest to ddflow's queue (JSON task graph + MCP), and the only one with real dependencies in a machine format.

**Agent OS** ([repo](https://github.com/buildermethods/agent-os)). V: MIT, 5.5k stars, 132 commits; discovers/deploys "standards", shapes specs. U: last release date; looks quieter than the others (I): low priority.

**Tessl** ([registry](https://tessl.io/registry), [review](https://codemyspec.com/blog/tessl-review)). Per secondary sources: spec-as-source bet, MCP-plugged framework, registry with 3,000+ skills; framework in closed beta (U: may be out of date). No open file format I could verify: do not build against it.

**Others** (secondary source, U): GSD, Traycer, Augment Intent, cc-sdd exist per a [landscape article](https://codemyspec.com/blog/spec-driven-development); not researched further.

## 2. Comparison of artifact models

| Tool | Spec | Plan/design | Task list | Machine-readable bits | Who executes | Hook points |
|---|---|---|---|---|---|---|
| Spec Kit | `specs/NNN/spec.md` (stories, `[NEEDS CLARIFICATION]`) | `plan.md` + research, data-model, contracts/ | `tasks.md`: `- [ ] T001 [P] [US1] text path` | checkbox, T-ID, `[P]`, `[USn]`; deps only as prose/phases | the user's agent via `/speckit-implement` | extension hooks after_*; presets; CLI `specify`; constitution |
| Kiro | `requirements.md` (EARS) | `design.md` | `tasks.md` checkboxes, wave execution | EARS grammar, checkboxes, requirement refs (U) | Kiro agent (IDE/CLI/web) | steering, JSON hooks |
| OpenSpec | `openspec/specs/**` + delta specs | `design.md`, `proposal.md` | `tasks.md` numbered checklist per change | ADDED/MODIFIED/REMOVED headings, scenarios, archive | user's agent via `/opsx:apply` | schemas (artifact graph), adapters |
| BMAD | PRD | architecture doc | stories, `sprint-status.yaml` | YAML status | persona agents | modules/skills |
| Taskmaster | PRD text | n/a | `tasks.json` | full JSON graph, MCP | the editor's agent | MCP, config |
| Agent OS | product mission/specs | standards | spec task lists | markdown | user's agent | commands |
| Tessl | spec files (U) | n/a | n/a | n/a (closed beta) | agent via MCP | MCP |

Common denominators across all: markdown on disk, checkbox tasks, no lease, no multi-agent claim, no gate or independent-review concept, no event log. Gaps ddflow fills: leases and worktrees, `needs`/`globs` conflict model, gate pipeline with cross-family review, replayable event log, decisions/lessons. Gaps they fill that ddflow lacks: authored requirements and design as first-class prose artifacts, brownfield delta specs (OpenSpec), user-story traceability, EARS (ddflow already plans EARS in PD-req-kind), a large installed base of slash-command conventions.

## 3. What ddflow already has to reuse (read from the queue today)

- **Queue model**: tasks with `needs`, `globs`, `--parent` subtasks, `--phase`, `--tags`, priority, release line; `ddflow task add --check` dedup; `ddflow plan check <file>` takes a TOML or JSON plan (I tried a markdown checklist: refused as "neither TOML nor JSON"); `ddflow import [--apply|--verify]` imports from existing markdown TODO-style sources with drift report (`--verify`), capped by `[importer] max_tasks`; `ddflow external sync` observes sibling-repo `needs`.
- **Export** (`ddflow export`, phase B-export): one-way generated documents (roadmap, bugs, rules, status, worklog, sessions, decisions, changelog) with modes whole/region/append, `--check`, `--diff`, `--update`, enable/disable/eject. A `tasks.md` kind would be one more export kind.
- **Document types** (P-docs-generic, P-doc-templates): B-dt-spec (TOML doctype: path or collection pattern `{seq}-{slug}`, sections with ownership, sources, triggers, `extends`), B-dt-interview (ask sections; agent cannot answer human sections), B-dt-validate, B-dt-drafts (draft until accepted; cross-family acceptance knob), B-dt-ownership (generated/human/seeded/draft regions), B-tpl-regions/merge (3-way merge of hand edits), B-dt-obligations (doc due when code globs change), B-dt-events. A Spec Kit `spec.md`/`plan.md` is a collection doctype instance; nothing new in the engine.
- **Requirements** (P-product-docs): PD-req-kind (id, EARS text, source required, acceptance, verification, parents, person-only approval), PD-trace (suspect links, requirement to task to test), PD-lint (EARS parsability), PD-baseline/req-change. These are the native analogue of Kiro `requirements.md` and Spec Kit `spec.md`.
- **Import of other agents' files**: P-ai-import; OB-native-only already lists "Kiro specs, Spec Kit and OpenSpec trees" as pointer-plus-digest records, never emitted. Note: D-ai-import says these are now "managed like the rest (import and emit)", so P-sdd must align with OB-native-only rather than duplicate it.
- **Agents and execution**: P-agent-parity.launcher, AC-client (ACP; injects the ddflow MCP server into `session/new`), AC-agent (ddflow slash commands /claim /next /status /brief to ACP editors), P-agent-parity.permissions, P-fleet FL-tracker-contract (a contract an external orchestrator drives ddflow through) and FL-receipt (proof-of-work posted to PR/tracker), P-labels (LB-waves: waves as labels), P-extension-seams (typed registry, out-of-process plugin protocol, ETL Stage interface for importers/exports).
- **Governance**: D-roadmap-review (accepted 2026-10-14): pure-merge consolidations allowed, new areas built at queued priorities, reviewer independence rule, README front-door-only (so docs for P-sdd go in fragments, not new README sections). Standing rules: everything configurable by file/CLI/MCP/prompt, every definition a record kind (RC1-RC12), files under 20 KB, reuse shared engines.

## 4. Mapping

| Spec Kit / Kiro / OpenSpec | ddflow |
|---|---|
| constitution.md / steering / AGENTS.md | rules (RULES export) + decisions (D-*, with `Governs:` globs). Steering `fileMatch` = rule globs; `always` = unscoped rule. Import as rule candidates (via P-ai-import), never as decisions (decisions need an operator) |
| spec.md / requirements.md / OpenSpec specs | requirement records (PD-req-kind) or a doctype instance (B-dt-spec) under a new `[sdd]` doctype `spec`. Stories and EARS lines become requirement records; the file stays the human-readable view |
| plan.md / design.md | doctype `plan` (collection) linked to a phase; research.md, data-model.md map to research notes/doctype sections |
| tasks.md | phase (one per spec/change) containing tasks; each `- [ ] Tnnn` = task with `--parent` or phase membership |
| T001 id | task id with a prefix namespace (e.g. `sdd-<feature>-T001`); original id kept in a task field/tag for round-trip |
| `[P]` | I: only a hint. ddflow derives parallelism from disjoint `globs` and absent `needs`; import maps `[P]` to "no need on previous sibling" and requires globs extracted from the file paths in the text; where no path is present, mark globs unknown and refuse to schedule in parallel |
| `[US1]` | label/tag (P-labels) and a requirement link |
| Dependencies prose / phases (Setup, Foundational) | `needs` between tasks (phase barrier = each task in phase N needs the phase N-1 tasks) |
| checklist / acceptance criteria / scenarios | task body acceptance list + the gate pipeline; for requirements, the `acceptance` field and a test reference (bug rule: regression test) |
| `[NEEDS CLARIFICATION]` | ask section (B-dt-interview) or a question to the operator; blocks the draft's acceptance |
| `/analyze` consistency | `ddflow plan check` plus PD-trace suspect links and doc validate |
| clarify / checklist quality commands | doc validate, PD-lint |
| OpenSpec delta + archive | requirement supersede (B-rc-lifecycle) + baseline; archive = phase done and export snapshot |
| Kiro hooks / Spec Kit hooks | ddflow hooks (`ddflow_hooks`) and gates; map `after_tasks` to "run plan import" |
| `/implement` run by the agent | claim, worktree, gates, merge, complete |

## 5. Integration options, ranked by value over cost

**A. Import INTO ddflow (spec tree to phase and tasks). Highest value, low cost.**
First version: `ddflow sdd import <dir|tool>` dry-run by default, `--apply` like `ddflow import`. Parse Spec Kit `specs/*/tasks.md` first (most regular grammar: ID, `[P]`, `[US]`, checkbox, paths), then OpenSpec `changes/*/tasks.md`, then Kiro. Output a plan in the existing TOML/JSON form and run it through `ddflow plan check`, so no new validation path. Unchecked boxes become open tasks, checked ones are skipped unless `--include-done`. Spec/plan files are attached as a pointer plus digest (as OB-native-only does), not copied. Re-import = `--verify` style drift report, updating nothing silently.
Risks: id schemes (namespace prefix, collision with `task add --check` dedup), `[P]` without paths gives unsafe parallelism, imported prose is untrusted (record trust=imported, hazard scan from OB-safety), a task list regenerated by `/speckit-tasks` after import diverges. Configurable: source tool and paths, id prefix template, phase naming, `[P]` policy (hint|strict|ignore), max tasks, whether done tasks import, label mapping, which sections become needs.

**B. Export ddflow queue AS tasks.md (read-only view). High value, low cost.**
A new export kind `sdd-tasks` (flavours: speckit, openspec, kiro) rendering one phase to `specs/<NNN>-<phase>/tasks.md`, region mode so human prose stays; ddflow owns the checkbox state. Checkbox follows task state (done = `[x]`), `[P]` emitted only when the task has no `needs` on earlier siblings and globs disjoint. Reuses `ddflow export` enable/--check/--diff and B-tpl-regions. Value: Spec Kit/Kiro/OpenSpec users and their agents see progress with no ddflow knowledge, and `/speckit-implement` or `/opsx:apply` reading it will see completed tasks. Risk: an agent editing the checkboxes by hand (rule already exists: never edit inside a ddflow region; refuse and report via B-tpl-merge). Configurable: flavour, target path, which phase, id format, `[P]` emission, include done.

**C. ddflow as the executor behind `/implement` (MCP). High value, medium cost; do after A and B.**
Ship an extension/preset (Spec Kit extension with `speckit.ddflow.implement` command, OpenSpec/Kiro equivalent written by the onboarding adapter) whose body says: call `ddflow_brief`, `ddflow_next`, `ddflow_claim`, work, `ddflow_gate_status`, `ddflow_merge`, `ddflow_complete`, instead of ticking boxes. Technically this is a markdown command file, so P-agent-parity descriptors plus the OB emit/manifest machinery already cover writing and reverting it. A second variant uses FL-tracker-contract/AC-client so ddflow drives the agent. Risk: two truths if the user still ticks boxes by hand; permissions (the extension must not widen agent tool lists: operator-approved by digest per D-roadmap-review); Spec Kit extension API churn (hooks and manifests changed within months). Configurable: command name, which Spec Kit hook triggers it (`after_tasks` import prompt, optional=true), auto-import on `after_tasks`.

**D. ddflow documents AS Spec Kit specs (doctype `spec`, `plan`, constitution view). Medium value, low marginal cost once B-dt-* ships.**
Shipped doctypes in the B-dt-library style: `sdd-spec` at `specs/{seq:03}-{slug}/spec.md`, `sdd-plan`, plus `constitution` as an export of rules/decisions into `.specify/memory/constitution.md` (region mode). Sections human-owned with interview and `[NEEDS CLARIFICATION]` as ask placeholders. Value is that a ddflow-native project can emit a layout Spec Kit commands accept, and the draft/accept model gives provenance Spec Kit lacks. Risk: Spec Kit's template format changes under us (U: no stability promise seen); constitution drift if humans edit it (3-way merge handles). Do not start before B-dt-spec, B-dt-ownership, B-dt-interview exist. Configurable: paths, numbering, which rules export to the constitution, section mapping.

**E. Two-way sync. Defer.** Two sources of truth is the largest risk. If ever: checkboxes flow ddflow to file; new tasks flow back only via explicit import with a review diff (OB-roundtrip/OB-drift model).

**F. Prompt-level route.** A `/sdd` prompt template (overridable under `.ddflow/prompts`) walking any agent through `task add` and `plan check`; near-zero cost.

## 6. Proposed tasks for phase `P-sdd` (priority bands: 1 first, 3 last)

All carry the standing CONFORMANCE clause (knobs by file/CLI/MCP/prompt, record kinds under RC1-RC12, shared engines, files under 20 KB, deltas in docs fragments not the README).

1. **SDD-decide** (band 1; needs none): ask the operator the open questions in section 7, file a decision. Gate for everything else.
2. **SDD-probe** (band 1; needs SDD-decide): pin the formats against installed `specify`, `openspec` and Kiro versions: generate a Spec Kit project, an OpenSpec change and a Kiro spec in a temp dir, record real tasks.md output as fixtures with version and date; verify what this report marks U (Kiro task markers, OpenSpec JSON flags, `workflows`). Mirrors AC-spike and P-agent-parity.drift.
3. **SDD-model** (band 1; needs SDD-probe, P-shared-engines record-kind tasks (SE-kind-data), B-rc-participation): normalisation model: a `spec-source` record kind (tool, path, digest, version, trust=imported) and the task-field/label mapping (origin id, story, `[P]` hint). One declaration gets history, search and RAG export.
4. **SDD-parse** (band 1; needs SDD-model, OB-safety): pure parsers for Spec Kit, OpenSpec and Kiro tasks.md to typed records; extract ID, checkbox, `[P]`, story, paths, nesting; fixtures from SDD-probe; hazard scan on input.
5. **SDD-import** (band 1; needs SDD-parse, P-extension-seams.stage): `ddflow sdd import|verify` on CLI, MCP and prompt; emits a plan through `ddflow plan check`; dry-run default; id prefix, `[P]` policy, done handling as knobs; `--verify` drift report.
6. **SDD-export-tasks** (band 2; needs SDD-parse, B-tpl-regions, B-tpl-merge, B-export): `sdd-tasks` export kind with speckit/openspec/kiro flavours, region mode, checkbox sync from task state, `[P]` derived from disjoint globs and no `needs`.
7. **SDD-rules-bridge** (band 2; needs OB-model, OB-rules-glob): constitution and steering files <-> rules: import as rule candidates (operator accepts), export rules plus selected decisions into `.specify/memory/constitution.md` and `.kiro/steering/*.md` with front-matter mapping.
8. **SDD-executor-ext** (band 2; needs SDD-import, OB-emit-manifest, P-agent-parity.permissions, B-sub-trust): emit a Spec Kit extension and OpenSpec/Kiro command files making `/implement` run the ddflow loop; digest manifest, revert, consent; the hook that runs import after `after_tasks` is optional=true.
9. **SDD-doctypes** (band 3; needs B-dt-spec, B-dt-interview, B-dt-ownership, B-dt-validate, PD-req-kind): shipped `sdd-spec`, `sdd-plan` doctypes producing Spec Kit layout, stories to requirement records, `[NEEDS CLARIFICATION]` to ask sections.
10. **SDD-trace** (band 3; needs SDD-doctypes, PD-trace): requirement to task to test links across imported specs, suspect marking when spec.md digest changes (the `/analyze` equivalent).
11. **SDD-acceptance** (band 3; needs SDD-executor-ext, SDD-export-tasks): fixture project with a real Spec Kit tree: import, claim and complete two tasks via a fake agent (AC-fake-agent), export, re-import is a no-op.
12. **SDD-eval** (band 3): seeded hazards (shared-file `[P]`, duplicate T-ids, injected text, hand-edited region, regenerated spec) plus docs fragment and help topic, no README section.

## 7. Open questions for the operator

1. Direction priority: is import (A) plus export view (B) enough for now, or is `/implement`-via-ddflow (C) the real goal?
2. Which tools first? Spec Kit and OpenSpec only, or Kiro as well (proprietary, format partly unverified)? Drop BMAD, Agent OS, Tessl as non-targets?
3. Source of truth when both exist: is ddflow always authoritative for task state, and the spec files always authoritative for requirement prose?
4. May an agent run `sdd import --apply` unprompted, or does import need operator approval (imported prose is untrusted; the task list becomes executable work)?
5. Should imported constitution/steering become rules automatically, or only as candidates the operator accepts (I recommend candidates)?
6. `[P]` without paths: refuse parallel, or let an agent infer globs (then reviewed)?
7. Id scheme: keep Spec Kit's T001 with a feature prefix, or generate ddflow ids and store the origin id in a field?
8. Do we maintain a Spec Kit extension in the public catalog, with the churn of weekly Spec Kit releases, or only emit local command files?
9. Does P-sdd absorb OB-native-only's Kiro/Spec Kit/OpenSpec items, or stay separate and depend on it?

## 8. Not verified

Spec Kit `workflows` semantics and current core command set; Kiro optional-task markers; OpenSpec JSON output and cadence; Tessl availability; Agent OS activity; Kiro licence. On the ddflow side I used list/show/--help only, not source, so reuse of `export`/`import` internals is inference.

Sources: [Spec Kit repo](https://github.com/github/spec-kit), [Spec Kit docs](https://github.github.com/spec-kit/), [releases](https://github.com/github/spec-kit/releases), [tasks template](https://raw.githubusercontent.com/github/spec-kit/main/templates/tasks-template.md), [extension guide](https://github.com/github/spec-kit/blob/main/extensions/EXTENSION-DEVELOPMENT-GUIDE.md), [OpenSpec](https://github.com/Fission-AI/OpenSpec), [Kiro specs](https://kiro.dev/docs/specs/), [Kiro steering](https://kiro.dev/docs/steering/), [Kiro hooks](https://kiro.dev/docs/hooks/), [BMAD](https://github.com/bmad-code-org/BMAD-METHOD), [Taskmaster](https://github.com/eyaltoledano/claude-task-master), [Agent OS](https://github.com/buildermethods/agent-os), [Tessl review](https://codemyspec.com/blog/tessl-review), [landscape](https://codemyspec.com/blog/spec-driven-development).
