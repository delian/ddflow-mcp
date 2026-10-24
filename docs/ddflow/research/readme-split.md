# README split research (readme-research)

Research for `readme-research` (phase P-readme, kind research), the input to `readme-decide`. Question: how does README.md become ddflow's first managed document — a primary front door plus referenced docs — by reusing the PLANNED doc-management machinery, not a parallel regime.

Probes run: `ddflow show readme-research / readme-decide / readme-manage / readme-split / RH-hot-globs`, `ddflow decision show D-readme-split / D-pipeline-ideas-r2`, `ddflow list task` (x2 pages), heading/size pass over README.md, marker scan (`ddflow:begin/end`), `.ddflow/pins.toml`, `ls tests/test_readme* test_doc* test_doctype*`.

## 1. README.md inventory

6778 lines, ~460 KB. Hand text plus five generated regions. Every h1/h2 section with its size and proposed home. Homes: K = stays in the primary front door; the rest move to a referenced doc per the audience map of D-readme-split (docs/guide/*.md, see §3 gap G1) or are generated.

| Lines | Lines# | Section | Proposed home |
|---|---|---|---|
| 21 | 1-21 | `# ddflow` (title, one-liner) | K |
| 42 | 22-63 | Instruction for an agent reader | K (agent brief) |
| 27 | 64-90 | Everything is yours to change, per project | K |
| 66 | 91-156 | Introduction (problem, what changes, who for, what it is not, a first run) | K |
| 26 | 157-182 | Install-from-repository block (PyPI note) | K (quickstart) |
| 61 | 183-243 | How do I…? | K |
| 127 | 244-370 | Table of contents — **generated** (`README/toc`, pin `readme-toc` → `ddflow.views.readme_toc`) | K, generated |
| 30 | 371-400 | Help: what it can do, and the workflow | K |
| 79 | 401-479 | The default workflow at a glance | K |
| 265 | 480-744 | Two ways to drive it (standalone; `README/commands` **generated**; MCP; AGENTS.md block) | docs/guide/cli-mcp.md |
| 4 | 745-748 | The workflow, and changing it | docs/guide/workflow.md |
| 9 | 749-757 | The workflow this project runs (literal) | docs/guide/workflow.md |
| 77 | 758-834 | The rules, and where each came from | docs/guide/workflow.md |
| 29 | 835-863 | Why it is built this way | K (benefits) |
| 59 | 864-954 | Install into any project | K keeps a short install pointer; full text → docs/guide/adoption.md |
| 700 | 955-1772 | Install subsections: Docker; config.toml/macros.toml; what is committed; publishing and registry; cutting a release; what CI checks; any LLM as a reviewer; companion tools (955 docker, 992 extending-by-text, 1117 committed, 1158 publishing, 1231 release, 1291 CI, 1347 reviewers, 1634 companions) | docs/guide/adoption.md (install+import) and docs/guide/contributing.md (publishing/release/CI/reviewers) |
| 26 | 1773-1798 | Pin files regenerate | docs/guide/workflow.md |
| 37 | 1799-1835 | Finding a test polluter | docs/guide/workflow.md |
| 110 | 1836-1945 | Wiring it into your agent (incl. one set of rules) | docs/guide/adoption.md |
| 47 | 1946-1992 | From plan mode to the queue | docs/guide/workflow.md |
| 31 | 1993-2023 | When a companion is missing | docs/guide/adoption.md (companions) |
| 201 | 2024-2224 | Adopting a project that already has history (incl. what it won't decide, unticked boxes, verifying) | docs/guide/adoption.md |
| 61 | 2225-2285 | The model: phases, tasks, dependencies, globs | docs/guide/workflow.md |
| 32 | 2286-2317 | Work that changes shape while you do it | docs/guide/workflow.md |
| 22 | 2318-2339 | Architectural decisions | docs/guide/workflow.md |
| 294 | 2340-2633 | Recall (embedder, guidance path, provenance, similar, dupes, the check every add runs) | docs/guide/knowledge.md |
| 18 | 2634-2651 | Operational memory | docs/guide/knowledge.md |
| 35 | 2652-2686 | Resources and long-running jobs | docs/guide/workflow.md |
| 14 | 2687-2700 | Dependencies on another repository | docs/guide/workflow.md |
| 72 | 2701-2772 | Status, progress, and loops | docs/guide/workflow.md |
| 268 | 2773-3040 | The task pipeline (negatives, scoped gates, evidence, exit codes) | docs/guide/workflow.md |
| 19 | 3041-3059 | The phase pipeline | docs/guide/workflow.md |
| 70 | 3060-3129 | Human approval (agates.toml) | docs/guide/workflow.md |
| 378 | 3130-3507 | Parallelism and coordination (lanes, adaptive, host signals, flow signals) | docs/guide/parallelism.md |
| 239 | 3508-3746 | Gitflow, pull requests and version tags (release lines, environment branches, workflow choices) | docs/guide/gitflow.md |
| 118 | 3747-3864 | Many agents, one server: identity, state and sharing (stateless, who is calling, several projects, locking) | docs/guide/parallelism.md |
| 36 | 3865-3900 | Crash recovery | docs/guide/upgrading.md or pointer to docs/RECOVERY.md |
| 45 | 3901-3945 | Reconstruction from logs alone | same |
| 38 | 3946-3983 | Lessons that check themselves | docs/guide/knowledge.md |
| 34 | 3984-4017 | Checking that the checks are working | docs/guide/workflow.md |
| 697 | 4018-4714 | Reading the log … upgrading … version stamp … data repairs … upgrade manifest … migrations … applying an upgrade … the compaction that was declined | docs/guide/upgrading.md |
| 202 | 4715-4916 | Lessons, research and bugs (bugs as queue items, listing, searching) | docs/guide/knowledge.md |
| 40 | 4917-4956 | Cadences | docs/guide/workflow.md |
| 42 | 4957-4997 | Scheduled jobs (bug-audit.toml) | docs/guide/workflow.md |
| 51 | 4998-5048 | Event triggers (red-gate.toml) | docs/guide/workflow.md |
| 285 | 5049-5333 | Exporting documents | docs/guide/customisation.md |
| 49 | 5334-5382 | Keeping AGENTS.md true | docs/guide/adoption.md |
| 52 | 5383-5434 | Surviving a compaction | docs/guide/workflow.md |
| 26 | 5435-5460 | Keeping session-start cost flat | docs/guide/workflow.md |
| 48 | 5461-5508 | Agent portability — `README/agents` **generated** (pin `readme-agents` → `ddflow.surfaces.harness_table`) | text → docs/guide/cli-mcp.md; region generated |
| 225 | 5509-5733 | Keeping the two surfaces honest — `README/tools` **generated** (pin `readme-tool-table` → `ddflow.surfaces.tool_table`) | text → docs/guide/cli-mcp.md; region generated |
| 127 | 5734-5860 | Command reference (points at `--help`; the full `README/commands` generated table lives under "Standalone") | docs/guide/cli-mcp.md; generated |
| 484 | 5861-6344 | Configuration — `README/knobs` **generated** (411 lines; pin `readme-knobs` → `ddflow.views.knob_table`) | docs/guide/configuration.md; region generated |
| 43 | 6345-6387 | Id schemes | docs/guide/configuration.md |
| 196 | 6388-6583 | What is automated, and what is not | docs/guide/workflow.md |
| 113 | 6584-6696 | Customisation reference (editable files, config sections, prompts, commands, MCP tools, env vars) | docs/guide/customisation.md |
| 70 | 6697-6766 | Testing | docs/guide/contributing.md |
| 12 | 6767-6778 | Documentation index (hand-kept table of docs/*.md) | K (pointer list, regenerated by the split) |

Generated regions in README.md today: `README/toc` (244-370), `README/commands` (544-640), `README/agents` (5475-5503), `README/tools` (5575-5730), `README/knobs` (5930-6341). All five are declared pins in `.ddflow/pins.toml` and have a staleness test each (test_readme_toc.py, test_readme_command_table.py, test_readme_agents_table.py, test_readme_tool_table.py, test_readme_knob_table.py). Non-region tests pinning README content: tests/test_readme_catchup.py (every ddflow command and MCP tool named at least once) and tests/test_readme_agent_reader.py.

## 2. Machinery to reuse (exists, do not re-invent)

Decisions (event log is the source of truth):
- **D-readme-split** (accepted, operator 2026-10-12): README stays the front door (what it is, install, quickstart, How do I…?, pointers); the reference moves to `docs/guide/*.md` by audience: workflow and gates, upgrading, parallelism and coordination, gitflow and PRs, recall/lessons/bugs, import/adoption, MCP and CLI reference, configuration, customisation. Every moved section keeps a stable anchor (a redirect line in the README); the knob table, TOC and tool tables stay generated regions; tests pinning README strings and D-readme-current / the docs gate allowlist follow the new files; per D-roadmap-review item 5, each later change edits one fragment (P-change-surface.docs-fragments).
- **D-roadmap-review** item 5: README front door only; each task updates its docs fragment, generated tables included; D-readme-current superseded; RH-hot-globs rewrites task globs.
- **D-pipeline-ideas-r2**: document contract classes evergreen/point_in_time/generated; a `[docs.classes]` glob-to-contract map with shipped defaults (README/ARCHITECTURE/SECURITY/CONTRIBUTING evergreen; ADR/plans/specs/decisions/CHANGELOG/HISTORY/research point_in_time; region-owned exports generated); doc.reviewed stamp in the log; `[docs].review_requires = cross_family`; safe-class auto-fix floors; `[enforce].doc_exclude` keeps meaning "not checked".
- **D-doc-types** (accepted; `ddflow/services/doctypes/**` SHIPPED): types shipped → `.ddflow/doctypes/<name>.toml` → `[docs.types.<name>]`, ownership generated|human|seeded|draft, cross-family doc review, "ddflow docs" group.
- **D-doc-regions** (accepted): one marker grammar `<!-- ddflow:begin <doc>/<region> sha=h -->` for every managed region; hand edits 3-way merged.
- **D-universal-principles → enforcement** (D-shared-engines-r2 item 9): hand edits to the five hot files — README.md, config.py, mcp.py, events.py, model.py — fail the standards gate unless on the exemption list with a reason; SE-conformance and SE-reuse-guard block at the standards gate.

Shipped code the split can configure, not code: `ddflow/services/doctypes/` (spec, find, validate, rubrics, sources incl. fragments), `ddflow docs check` (`[docs].check_paths`), region engine + pins (`ddflow pins regenerate`), docs index (test_docindex.py manages README + docs/*.md at import).

Queued planned tasks the split/registration reuses (from `ddflow list task` + the roadmap research):
- readme-decide → readme-manage → readme-split (the chain this doc feeds).
- B-tpl-readme-regions (open): README regions (knob table, TOC, command table) use the shared region engine — consumes B-tpl-readme-regions; consumers add needs on it.
- RH-hot-globs (open): narrow the coarse hot-file globs, README.md included (~63-71 open tasks hold it); "a doc update lists its docs fragment (D-readme-split)"; plan check refuses new hot globs.
- P-change-surface.docs-fragments (open): each change adds a fragment next to its feature; generated regions assemble the README and docs.
- B-dt lint/validate/spec/sources/rules — spec, rules, validate, sources done; B-dt-lint-config open; P-docs-generic, P-doc-templates, P-docs-search, P-roadmap phases still open (B-dt-unify open, needs B-tpl-readme-regions among others).
- SE-conformance and SE-reuse-guard (open) are the standards-gate blockers of D-universal-principles item 9.
- P-change-surface.pins-size/pins-split (open): docs/ddflow/RESEARCH.md, BACKLOG.md splitting — a sibling precedent for per-topic docs with generated indexes.

## 3. Contradictions and gaps (probe results)

- **G1 — referenced-doc paths disagree.** D-readme-split (accepted) says the moved reference lives at `docs/guide/*.md`; readme-split's own globs say `docs/ddflow/readme/*.md, docs/ddflow/guides/*.md`. Decide must record one; the task globs and the D-readme-split Governs line then match. Observation: this repo's existing layout is `docs/` (ARCHITECTURE, RECOVERY, RESEARCH, HANDOFF, BACKLOG) plus `docs/ddflow/` (drivers, decisions, research, LESSONS, QUEUE, adding-a-harness, compatibility) — there is no docs/guide/ yet, so whichever spelling wins is new.
- **G2 — sections without a home in the 9-audience list.** Publishing/registry, cutting a release, CI checks, reviewers, companion tools, testing, dependencies-on-another-repository, crash recovery/reconstruction (docs/RECOVERY.md already exists) fit none of the nine audiences exactly. Proposals below; decide picks.
- **G3 — a generated region inside hand prose.** README.md is one file whose doc class is "evergreen" but whose five regions are "generated". How the `[docs.classes]` glob map expresses a compound file (README.md = evergreen, README/* = generated) must be answered; likewise whether the front-door prose and the generated tables survive in the same file after the split or split apart (P-change-surface.docs-fragments assembles, B-tpl-readme-regions renders regions — assembling FROM where is the open question).
- **G4 — the readme-catchup contract moves how?** tests/test_readme_catchup.py asserts every CLI command and MCP tool is named at least once in README.md. Once the cli-mcp reference moves to a referenced doc, the test, its pin and the docs-gate allowlist (D-readme-split says they "follow the new files") have to move with it; decide should record the enforcement target.
- **G5 — the migration window serialises README.md.** More than 63 open tasks hold a README.md glob today, and D-readme-split orders the split "after the generated TOC task merges and while no agent is editing README.md" — P-readme-structure.toc is done (2026-10-12), so the "after the TOC task" precondition is met, but the "no agent editing README.md" window must be re-checked at the moment (RH-hot-globs shrinks the holders; the split itself must run as a docs kind item with a clean README lease). readme-decoration is that serialisation point; decide records the order.
- **G6 — readme-decide records "Decision D-readme-split" but D-readme-split already exists** as an accepted record. readme-decide must ADD the new record (or an -r2 amendment) naming the class map, gate and migration order, not edit the accepted one; its glob is docs/ddflow/decisions/readme-split.md.
- **G7 — `ddflow docs check` on this README** takes minutes (measured: still running at 120s wall when aborted) and pins `[docs].check_paths`; whether the referenced docs are added to check_paths, or a [docs.classes] entry implies the check, is for decide/readme-manage.

## 4. Proposal (draft for readme-decide)

### 4.1 Primary README (front door)

Title line; TOC (generated); the agent brief ("Instruction for an agent reader"); what ddflow is, benefits ("Why it is built this way" compressed), who it is for/not; quickstart ("A first run"/install from repo); "How do I…?"; the default workflow at a glance; a short list of pointers — one stable anchor line per moved section and per referenced doc, plus the pointer index that replaces the hand-kept Documentation index.

### 4.2 Referenced-doc map

`docs/guide/workflow.md` — workflow, rules, plan mode, the model, pipelines, approval, cadences, automation matrix, loops, test polluter, pins, jobs. `docs/guide/adoption.md` — install, Docker, wiring into an agent, adopting existing history, AGENTS.md true, companion missing. `docs/guide/contributing.md` — publishing, release, CI, reviewers, companions, testing. `docs/guide/cli-mcp.md` — standalone/MCP/AGENTS.md surfaces, command reference, the README/commands and README/tools regions. `docs/guide/configuration.md` — configuration, knobs (the 411-line README/knobs region), id schemes. `docs/guide/customisation.md` — customisation reference, exporting documents. `docs/guide/knowledge.md` — recall, memory, lessons/research/bugs. `docs/guide/upgrading.md` — reading the log, upgrading, repairs, skew, manifest, migrations, crash recovery/reconstruction. `docs/guide/parallelism.md` — parallelism and coordination, many agents one server. `docs/guide/gitflow.md` — gitflow, PRs, tags, release lines, environment branches.

(Per D-readme-split; exact workbook G1/G2 choices in §3.)

### 4.3 `[docs.classes]` entries

README.md → evergreen (shipped default, kept by an explicit entry); each docs/guide/*.md → evergreen; README/* regions → generated; .ddflow/pins.toml pin recipes → generated (their regions already are); Documentation index/tables → generated after the split. Compound-file semantics (G3).

### 4.4 Enforcement

Primary standard: README.md and the referenced docs are in `[docs.classes]` with their contracts; the evergreen/hot-file hand-edit rule blocks at the standards gate via SE-conformance/SE-reuse-guard exemptions model (already-open tasks); staleness is caught by the existing `ddflow pins regenerate --check` and the docs gate; cross-family doc review per D-doc-types. readme-manage wires the existing pieces together with config, no new enforcement code.

### 4.5 Migration order

1. Record the decision (readme-decide) — paths (G1), the map above, class entries, gates. 2. readme-manage: register every doc in `[docs.classes]`/doctypes config — README compact but still whole. 3. readme-split (kind doc): create the referenced docs from the current sections (anchors kept via one redirect line each), rewrite the primary README, move the pinned regions last so the five regenerators and their tests keep passing. 4. RH-hot-globs: rewrite task globs once the fragment layout exists; new tasks list their docs fragment. 5. Then each change edits one fragment (P-change-surface.docs-fragments).

## 5. Open questions readme-decide MUST answer

1. Referenced-doc path spelling (G1). 2. Buckets for sections outside the nine audiences (G2). 3. Where the five generated regions physically live after the split (stay in README vs follow the cli-mcp/configuration docs) and the assembler (G3). 4. The target of the readme-catchup/all-surfaces-named check after the move (G4). 5. The migration window: what workstreams must hold README.md while readme-split runs. 6. Whether readme-manage may reuse the shipped doctypes/docs-check machinery as-is or must declare needs on the open B-dt tasks (B-tpl-readme-regions et al.) which are still in the docs-wave. 7. Whether the linked-in docs (README → docs/guide/*) count as "regions ... followed by the docs gate allowlist" — i.e. the docs gate allowlist update. 8. The stable-anchor convention: one redirect `<details>` line per moved section vs a dedicated pointer section.
