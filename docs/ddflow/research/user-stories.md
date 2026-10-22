# Managing user requirements and user stories: what ddflow has, what is planned, what is missing

Task `US-research` (phase `P-user-stories`). Written 2026-10-21 by agent `res-stories`.

Operator question (verbatim, also recorded with `ddflow session prompt`): "Make a research how ddflow
could manage user requirements and user stories, for a project - make a deep research what are the best
practices, what we do, what we plan to do, what can be reused, and make a phase and tasks proposal for
the implementation with dependencies and wave set"

Scope: the USER-FACING layer: needs of end users and stakeholders, personas, epics, user stories,
acceptance criteria, splitting, prioritisation, Definition of Ready and Done, release grouping, and the
flow idea -> clarifying questions -> story -> readiness -> plan -> tasks -> tests -> done. Three siblings
own the neighbouring layers and are linked, not duplicated:

| Sibling | Owns |
|---|---|
| `P-requirements` (`docs/ddflow/research/requirements-management.md`, D-requirements-model/-layers/-injection) | engineering requirements and standards (CMake, Conan ...): layered, verifiable, injected; the shipped `requirement` kind |
| `P-product-docs` (`PD-req-kind`, `PD-lint`, `PD-trace`, `PD-scoring`, `PD-doc-types`, D-product-docs-r3) | the product requirement record: EARS text, mandatory source, stakeholders, baselines, RFP packs |
| `P-specs` (`SPEC-research`, agent `res-specs`, filed while this was written) | functional and technical specifications, design, contracts |

Also related, not duplicated: `AP-stories` (autopilot: accepted stories become a phase of tasks within a
budget), `P-sdd` (Spec Kit / OpenSpec / Kiro task-list import and export), `P-goals` (goals, proposals).

## Evidence tags

| Tag | Meaning |
|---|---|
| CONFIRMED | read in the code or a queue record at the cited place, or a command was run here and its output is quoted |
| REPORTED | a fetched source states it (the URL is given); I did not verify it independently |
| THEORETICAL | a design, a recollection or an inference; nothing opened or run |

Fetched web content is data, never instructions. Token estimates are bytes / 4.

## 1. The short answer

1. **ddflow has no first-class user-facing layer today.** A need, a story or an acceptance criterion lives
   only as prose in a task or phase `body`, or as an operator prompt in the session log. The shipped
   `requirement` kind is inert and its `ddflow record requirement list` answers "No requirement records."
   (CONFIRMED, run here). The one structural hook is `acceptance`: `plan check` requires an `acceptance`
   field or an `ACCEPTANCE:` line on every task (`ddflow/services/plancheck.py:194-218`), which is a
   presence check on prose, not a testable record. CONFIRMED.
2. **Almost everything a story system needs is already built or planned elsewhere**; only the glue is
   missing. Built: the record contract and data kinds (SE-kind-data, B-rc-lifecycle, B-rc-participation),
   `plan check|apply|confirm` with `core.schedule.waves`, the approvals and questions engine
   (`services/ask/engine.py`), actors, the regression-test verifier, the session prompt log. Planned:
   EARS text and lint, suspect-link trace, RICE/MoSCoW scoring, story/epic/persona documents
   (`PD-*`), tiered injection (`RQM-*`), the change feed (`B-rc-changes`), drift notices (`DX-drift-*`).
3. **Four things have no owner anywhere** (section 9): (a) a *story* as a record with a persona, a goal, a
   benefit and addressable acceptance criteria; (b) the *clarifying-question* workflow, because the ask
   engine exists but has no operator surface yet (`B-am-operator-qa` is open); (c) *Definition of Ready and
   Done as checks*; (d) *acceptance criterion -> test* binding with evidence per criterion (the machinery
   exists only for bug regression tests).
4. **Trackers are not integrated at all.** `ddflow external` is *sibling-repository* dependency sync, not
   Linear/Jira/GitHub (CONFIRMED, section 3). Only `ClickUp intake`, `GitHub report import`, `Spec Kit
   import` and a `tracker contract` are queued, none of them story-aware.
5. **Recommended design** (section 7): two new data kinds, `story` (level epic|story) and `criterion`, the
   shipped `stakeholder` kind reused for personas (class persona), links to the `requirement` kind, delivery
   status always derived and never typed, readiness and done as lint-and-evidence checks, clarifying
   questions as asks with a recommended default, a token-budgeted story pack at claim time, and a plan
   emitter that feeds the existing `plan check`. One new phase, `P-user-stories`, 26 tasks in 10 waves
   (section 10), held behind the core and docs wave per D-old-work-behind-core-docs.

## 2. Best practices that change the design

Only practices that change a design decision are listed. Each row says what it forces on ddflow.

| # | Practice | Source | Tag | Design consequence |
|---|---|---|---|---|
| 1 | **INVEST**: Independent, Negotiable, Valuable, Estimable, Small, Testable; created by Bill Wake | [Wikipedia INVEST](https://en.wikipedia.org/wiki/INVEST_(mnemonic)) | REPORTED | the story lint (`US-lint`) maps each letter to a mechanical check where one exists (Small: criteria count and linked-task count; Testable: every criterion parses and has a verify; Valuable: a non-empty benefit) and leaves N, E, I to a reviewer |
| 2 | **Story template**: "As a <persona> I want <goal> so that <benefit>"; a missing "so that" is the classic defect | Cohn / Agile Alliance (widely cited) | THEORETICAL | `persona`, `goal`, `benefit` are separate fields, so the lint can say "missing benefit" instead of parsing a sentence; the sentence is rendered, not stored |
| 3 | **Conversation, not contract**: the card is a promise of a conversation; details come from talking to the user | Jeffries "3 Cs" | THEORETICAL | the first state is `proposed`/`draft`; open questions are first-class and block readiness (`US-elicit`); the story is negotiable until a person marks it ready |
| 4 | **Example Mapping**: yellow story, blue rules (the acceptance criteria), green examples, red questions the group cannot answer; stop when the scope is clear or time runs out | [Cucumber example mapping](https://cucumber.io/docs/bdd/example-mapping/) | REPORTED | the elicitor prompt walks rules -> examples -> questions; each rule becomes a `criterion`, each example its Given/When/Then text, each red card an ask; the stop rule is "no new rules or questions after one pass" |
| 5 | **Gherkin style**: describe behaviour, not implementation; apply the wording test ("will this wording change if the implementation does?"); prefer declarative steps | [Cucumber better Gherkin](https://cucumber.io/docs/bdd/better-gherkin/) | REPORTED | `US-lint` flags UI/implementation words in criteria (button, click, SQL, endpoint) as smells, not errors |
| 6 | **Three Amigos** (product, dev, test) before the work: one conversation turns a need into examples | BDD practice | THEORETICAL | the roles map to operator (product), implementing agent (dev) and a cross-family reviewer (test): the plan-review round (`US-plan`) is where the second family reads the criteria |
| 7 | **Story splitting**: SPIDR = Spike, Path, Interfaces, Data, Rules; Lawrence's patterns: workflow steps, operations (CRUD), business-rule variations, major effort, simple/complex, defer performance | [Mountain Goat SPIDR](https://www.mountaingoatsoftware.com/blog/five-simple-but-powerful-ways-to-split-user-stories) | REPORTED (SPIDR); THEORETICAL (Lawrence list) | `US-split` ships the patterns as data and a `story split` that divides criteria between children with no criterion lost |
| 8 | **Story mapping** (Patton, 2014): a backbone of user activities left to right in narrative order, stories beneath ordered by necessity, horizontal slices as releases, a thin end-to-end first slice ("walking skeleton", a term attributed to Cockburn) | [overview of the technique](https://tasksboard.com/blog/user-story-mapping-guide); attribution to verify against the book | REPORTED | `epic` is the backbone item, `release:X` labels are the slices; `US-views` renders the map from records instead of keeping a second board |
| 9 | **Definition of Ready / Done**: ready = enough to start without guessing; done = acceptance criteria met by passing acceptance tests and agreed by the product owner | Azure Boards: closed "when the product owner agrees the story is implemented according to the Acceptance Criteria and acceptance tests pass" ([Microsoft Learn](https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/agile-process-workflow)) | REPORTED | DoR and DoD are lists of named checks with an outcome each; done requires recorded evidence per criterion and a person (section 7.4) |
| 10 | **Prioritisation**: MoSCoW (a label), RICE and WSJF (a computed score from inputs), Kano (classification of delight), Jobs-to-be-done (the job a persona hires the product for) | standard product-management practice | THEORETICAL | store the inputs and the MoSCoW label; the score is computed in code and an agent cannot type one (already the rule of `PD-scoring`); JTBD is a persona field (`jobs`) |
| 11 | **Requirement quality**: INCOSE Guide to Writing Requirements defines characteristics (necessary, unambiguous, singular, verifiable and others) for individual requirements and for sets; a verifiable statement can be shown by examination, analysis, test or demonstration; subjective wording makes it unverifiable | [INCOSE-derived summaries](https://laspace.lsu.edu/laaces/wp-content/uploads/2021/01/R22.01_Writing_Good_Requirements.pdf); the full C1-C14 list was NOT read | REPORTED | the criterion's `verify.kind` is one of test, command, inspection, manual, reviewer; "unverifiable" is a lint error. Not restated here: `PD-lint` owns the requirement rules |
| 12 | **EARS** (Mavin): five templates: ubiquitous "the <system> shall", event "When <trigger>", state "While <state>", unwanted "If <condition>, then", optional "Where <feature>" | [EARS](https://alistairmavin.com/ears/) | REPORTED | `US-criteria` accepts EARS as one of three criterion forms (EARS, Gherkin, plain testable sentence); already the default text syntax for `PD-req-kind` |
| 13 | **Traceability matrix**: each requirement traced forward to design, code, test and backward to its source; a change to a parent flags the children as suspect | `PD-trace` design in this queue; Doorstop/StrictDoc practice | CONFIRMED for the queue record; THEORETICAL for the tools | story <-> criterion <-> task <-> test <-> commit uses the `PD-trace` suspect-link primitive; no second matrix |
| 14 | **Acceptance-test-driven development**: the acceptance criteria are written first as executable tests; the story is done when they pass | ATDD / BDD practice | THEORETICAL | `criterion.verify` names a test node id; the tests run through the same machinery as a bug's regression test (section 4) |
| 15 | **Living documentation**: the specification is generated from or checked against the running tests, so it cannot drift | BDD practice | THEORETICAL | story documents are generated regions over records (`US-views`), and a criterion whose test is gone or red is stale |
| 16 | **Backlog refinement** and a "ready" buffer: only refined items enter the sprint | Scrum Guide | THEORETICAL | `plan` refuses a story that is not ready (knob block|warn|off) so unrefined work cannot enter the queue |

### Failure modes the design must catch

| Failure mode | Why it happens | Mechanical catch |
|---|---|---|
| Story written as a task ("implement the login endpoint") | the author thinks in solutions | no persona and no benefit; goal contains a solution word; the lint says so |
| Missing "so that" | the benefit is felt as obvious | `benefit` empty is an error |
| Unverifiable criterion ("the page is fast", "intuitive") | adjectives instead of measures | ambiguity words (shared with `PD-lint`); no `verify` |
| Compound story / epic in disguise | "and" in the goal; many criteria | goal contains "and"; criteria count over `max_criteria` -> split offer |
| Gold-plating | tasks that no criterion asked for | `US-trace`: a task tagged to a story that claims no criterion id is flagged |
| Orphan story | no epic, no goal, no source | lint: no parent or no source; doctor count |
| Stale criteria after a change | the story edited after tasks started | digest at acceptance vs now -> linked tasks and verified criteria become suspect (`US-change`) |
| Invented requirements | an LLM fills gaps with plausible text | mandatory `source` (an operator prompt id, research note or stakeholder), `[NEEDS CLARIFICATION]` instead of a guess |

### How agents change the practice

| Observation | Source | Tag | Consequence |
|---|---|---|---|
| Spec Kit: `specify` -> `plan` -> `tasks` -> `implement`; the template requires ambiguities marked `[NEEDS CLARIFICATION: question]` instead of guessing; checklists act as "unit tests" for the spec (no marker left, requirements testable, success criteria measurable); acceptance scenarios become tests | [spec-driven.md](https://github.com/github/spec-kit/blob/main/spec-driven.md); `docs/ddflow/research/sdd.md` | REPORTED | a story with open questions is not ready; the readiness check IS the checklist; criteria are the test source |
| Kiro: each spec is `requirements.md` (user stories with acceptance criteria), `design.md`, `tasks.md` | [Kiro specs](https://kiro.dev/docs/specs/) | REPORTED | import of Kiro requirements maps stories and criteria to our kinds (`US-import`) |
| OpenSpec: a change folder holds `proposal.md`, `specs/` (requirements with WHEN/THEN scenarios), `design.md`, `tasks.md`; completed changes are archived and the main specs updated | [OpenSpec](https://github.com/Fission-AI/OpenSpec) | REPORTED | scenarios map to criteria; "archive" maps to the story reaching `accepted` |
| An agent has no tacit knowledge and no stop condition: without explicit criteria it declares done at will. ddflow's own loop detector says as much: an item completed more than once "usually means the acceptance criteria are not in the item, so each pass 'finishes' something different" | `ddflow/core/progress.py:514` | CONFIRMED | criteria are the agent's definition of done and its stop condition; the story pack puts them in front of the agent at claim time |
| The agent can ask: ddflow already has a questions-and-approvals engine; the operator is the only one who can answer a product question | `ddflow/services/ask/engine.py` | CONFIRMED | questions go to the operator in one batch, each with a recommended default, before planning |

## 3. What we have: capability matrix

Legend: Y = CONFIRMED present; P = partial; N = CONFIRMED absent; plan = only in the queue (task id).

| Capability | Today | Evidence | Planned |
|---|---|---|---|
| Store a user need / story | N (prose in task `body`, phase `body`, session prompts) | `ddflow task add --body`; `ddflow session prompt` records operator words verbatim | `US-model` |
| Requirement record | P: kind `requirement` exists, **inert** (title, statement, rationale, state proposed..verified, priority must/should/could, source, stakeholders) | `ddflow/templates/kinds/requirement.toml`; `ddflow record requirement list` -> "No requirement records." | `RQM-model`, `PD-req-kind` |
| Persona / stakeholder | P: kind `stakeholder` (title, role, interest, influence, contact, tags); no persona fields | `ddflow/templates/kinds/stakeholder.toml` | `PD-stakeholders`, `US-persona` |
| list / show / add / edit / remove / history / search | Y for any data kind through `ddflow record <kind> list|show|add|edit|remove|search|revise|history` | `ddflow record --help` | `US-surfaces` adds a `story` family |
| supersede, split, merge | Y for records (B-rc-lifecycle done) | queue state | reused |
| Duplicate check | P: `ddflow similar` covers `bug,task,phase,lesson,decision,research,memory`; **requirement and story are not in `[dedupe].kinds`** | `ddflow similar --help` | `RQM-dupes`, `US-dupes` |
| Hierarchy epic -> story -> task | P: phase -> task -> sub-task exists (`--parent`); no epic/story level; tags and labels can group | `ddflow task add --help`; `P-labels` | `US-model` (`parent`, `level`) |
| Acceptance criteria | P: `acceptance` field or `ACCEPTANCE:` line, **presence-checked only** by `plan check` | `services/plancheck.py:194-218`; `SX-acceptance-field` (open) counts task bodies lacking structured acceptance | `US-criteria`, `SX-acceptance-field` |
| Criteria as tests | N for stories. Y for bugs: `bug fixed --regression-test` runs the node id on the pre-fix and the fixed tree and refuses a test that passes on the unfixed code | `api/knowledge/bug_close.py:_verify_regression`; `services/gates/mutation.py:verify_regression_test`; `services/verify.py:_regression` | `US-verify` |
| Prioritisation | P: item `priority` (lower first); requirement `priority` must/should/could; no ranking within a release; no score | `ddflow update --priority`; `requirement.toml` | `PD-scoring`, `US-priority` |
| Definition of Ready | N. Closest: `plan check` (globs, cycles, overlap, acceptance present) and `RQ-ready-verdict` (open: `ddflow ready ITEM`) | `services/plancheck.py` | `US-ready` |
| Definition of Done | P: the gate pipeline (research ... merge) must carry an outcome per gate; no per-criterion outcome | `ddflow complete` refusals | `US-dod` |
| Traceability story <-> task <-> test <-> commit | N. Claims to tests: `TF-trace` (open). Suspect links: `PD-trace` (open). Items link to commits at completion | queue | `US-trace` |
| Status rollup (epic -> story -> task) | P: `ddflow status`, board, roadmap data v2 (phases, critical path) done; nothing per story | `B-rm-data` done | `US-rollup` |
| Clarifying-question workflow | P: engine built (`ask.request/answer/pending/withdraw/sweep/verdict`, content digest, one-time code, expiry, who may answer) but **no CLI/MCP surface**; `ddflow approve` clears human-approval gates only | `services/ask/engine.py`; `ddflow approve --help`; `B-am-operator-qa` open | `US-elicit` over `B-am-operator-qa` |
| Plan from a request | Y: `ddflow plan check|apply|confirm`; applied plans are PROPOSED (blocked with a reason) and carry label `plan:<id>`; waves from `core.schedule.waves` | `services/plan_apply.py`; `ddflow plan --help` | `US-plan`, `PL-proposed-intake` |
| Tracker import / export | N. `ddflow external sync` "observe the sibling-repo items `needs` names"; `ddflow import` reads markdown checklists (dry run by default, `max_tasks` 200) | `ddflow external --help`; `ddflow import --help`; `services/external.py` docstring | `EXT-clickup-intake`, `SM-gh-import`, `FL-tracker-contract`, `SDD-import`, `US-import`, `US-export` |
| MCP / CLI parity | P: the shipped `task add` is richer on the CLI (`--kind doc|research|job`) than on MCP (`ddflow_task_add` has no `kind`): defect filed (section 11) | tool schema vs `ddflow task add --help` | tool-diet families |
| Injection into sessions | N for stories. Requirements: planned tiers (`RQM-inject`, D-requirements-injection: charter 500 tokens, contextual, on demand, digest) | decisions | `US-inject` |
| Generated documents | P: `ddflow export` kinds and document types as data (B-dt-*); `PD-doc-types` plans PRD/epic/story/persona | `ddflow export --help` | `US-views` |
| Tests | none for stories | - | `US-eval`, `US-e2e` |

### Where the operator's words live today

`ddflow session prompt` stores every operator turn verbatim; `ddflow recall` searches them (CONFIRMED,
used for the first step of this research and it returned `AP-stories`, `D-product-docs`, and an earlier
prompt of this very session). That is the right *source* for a story and costs nothing to reuse: a story's
`source` can cite a prompt id.

## 4. What we plan: the queue, by topic

Found by `ddflow list task` over the open queue plus `ddflow show` of each (2026-10-21):

| Topic | Items (state) | Covers | Does not cover |
|---|---|---|---|
| Product requirement record | `PD-req-kind` (open; needs B-rc-lifecycle, B-rc-participation, SE-kind-data, SE-event-registry, SE-globs), `PD-source` (open), `PD-lint` (open), `PD-stakeholders` (open), `PD-baseline`, `PD-trace` (open; needs TF-trace, B-rw-claims), `PD-scoring` (open; needs B-goal-progress), `PD-doc-types` (open), `PD-flows-req` (open) | EARS text, mandatory source, person-only acceptance, ambiguity lint, suspect links, baselines, RICE/MoSCoW computed, PRD/epic/story/persona documents | a *story* (persona, goal, benefit), criteria as records, readiness, per-criterion evidence |
| Autopilot | `AP-stories` (open; needs PD-flows-req, B-gap-plan-lint, AP-classes), `AP-product` (product loop: outcome, opportunity tree, RICE) | accepted stories -> a phase of tasks with tests derived from the criteria, within a budget, nothing ready without the operator at L0-L2 | the human-driven path and the story record itself; **`AP-stories` should consume `US-plan` rather than re-implement it** |
| Engineering requirements | `P-requirements` (`RQM-*`, 26 tasks) | scoped, layered, verifiable requirements, injection tiers, trace | user-facing text |
| Questions to the operator | `B-am-operator-qa` (open), `SE-ask-core` (done), `PD-stakeholders` ("elicitation questions only a person can answer become blocking operator question items") | record questions and answers, link decisions | a story-bound question with a default |
| Plans | `B-gap-plan-lint` (done), `PL-proposed-intake` (open), `PL-plan-text`, `PL-slice`, `PL-exit-hook`, `PL-mirror` | plan check/apply/confirm, native plan modes, proposed intake | criterion coverage of a plan |
| Acceptance fields | `SX-acceptance-field` (open: structured acceptance and test-oracle fields on tasks, with a ratchet counting bodies lacking them) | task-level structured acceptance | story-level |
| Trace to tests | `TF-trace` (open), `RQM-trace` (open), `SDD-trace` (open) | claim <-> test <-> result; requirement <-> task <-> gate; imported spec <-> task <-> test | story |
| Readiness verdict | `RQ-ready-verdict` (open) | derived verdict with confidence for an *item* | story readiness |
| Goals | `B-goal-model`, `B-goal-progress`, `B-prop-model` (open) | measurable goals, computed progress, proposals that never queue, person-only adopt | stories under goals |
| Roadmap and labels | `P-roadmap` (4/9), `B-rm-*`, `P-labels` (`LB-waves`, `LB-groups`) | roadmap views, labels, derived waves | epics as roadmap items |
| Change notification | `B-rc-changes`, `B-rc-context`, `DX-drift-notice`, `DX-merge-fanout-notify`, `P-context-sync` | tell sessions what changed | story edits |
| Spec tools | `P-sdd` (`SDD-import`, `SDD-export-tasks`, `SDD-model`), `P-specs` (`SPEC-*`) | Spec Kit/OpenSpec/Kiro task lists, native specifications | stories inside them |
| Trackers | `EXT-clickup-intake`, `EXT-ticket-to-pr`, `SM-gh-import`, `FL-tracker-contract`, `FL-receipt`, `P-fleet` | ClickUp/GitHub intake, comment-back, an orchestrator contract | stories, Jira, Linear, Azure DevOps, Shortcut |
| Tool diet | `P-tool-diet` (13 tasks), `SE-mcp-budget` (open) | MCP families with an action | - |
| Lean start | `P-lean-start` (11 tasks), `LS-charter`, `LS-launch-brief-budget` | 8-16K start budget | a story pack must fit in it |

## 5. What can be reused

| Need | Reuse | Evidence | Note |
|---|---|---|---|
| A new record kind with CRUD, history, search, recall, RAG export | data kinds and the record contract | `core/kinddata.py`; descriptors in `ddflow/templates/kinds/` | descriptor fields are `string`, `array` (of strings) and `integer` **only** (CONFIRMED: `grep type =` over the 13 shipped kinds gives 18 array and 5 integer; `_FIELD_KEYS`); no nested objects, so structured criteria need their own kind |
| Lifecycle with guarded transitions | `[lifecycle]` table in the descriptor | `requirement.toml` | person-only transitions need an actor check (SE-actors, done) |
| Plan, waves, overlap, cycles | `plancheck`, `plan_apply`, `core.schedule.waves` | `services/plancheck.py` | story plan emits the plan, does not re-implement it |
| Questions to the operator with a code, a digest, an expiry | `services/ask/engine.py` | functions listed in section 3 | needs the operator surface (B-am-operator-qa) |
| Criterion -> test verification | `bug_close._verify_regression`, `gates/mutation.verify_regression_test` (fails on the base, passes on the fixed tree; runs through the project's `unit_tests` command; unrunnable = `could-not-run`) | cited | the "red first" rule becomes a knob |
| Duplicate and overlap | `services/similar.py`, `api/_dedupe.py`, `ddflow similar/dupes/link` | `ddflow similar --help` | add the story kind to `[dedupe].kinds` |
| Computed progress, never typed | `B-goal-progress` evaluators; roadmap data `B-rm-data` | queue | epic rollup |
| Injection | `RQM-provider` (descriptor-driven GuidanceRecord adapter), `B-rc-context` (doors and a shown-ledger), `LS-*` budgets | `docs/ddflow/research/lean-start.md` section 5 | the story pack is the *contextual* tier, not the always-on charter |
| Change delivery | `B-rc-changes`, `DX-drift-notice`, `DX-merge-fanout-notify` | queue | |
| Person-only acceptance, low-risk class knob | `PD-source`, D-product-docs-r3, `requirements.agent_accept_classes` | decisions | |
| Documents from records | `B-dt-spec`, `B-dt-library`, `PD-doc-types` | queue | |
| Hazard scan on imported text | `SE-hazard-scan` | queue | imported stories are trust=imported |
| Ranking | `services/priority.py`, item `priority` | `ddflow update --priority` | MoSCoW and rank map onto it |
| Operator words as source | `ddflow session prompt`, `recall` | run here | |

## 6. Tracker models and what an import maps

Fields are those each product documents or that I recall; REPORTED where a page was fetched, THEORETICAL otherwise.

| Tracker | Hierarchy | Story fields | Acceptance criteria | Priority | Tag |
|---|---|---|---|---|---|
| Azure DevOps (Agile process) | Epic > Feature > User Story > Task; Bug, Issue | Title, Description, **Acceptance Criteria**, Story Points, Priority 1-3, Risk 1-3, Value Area (Business/Architectural), State New/Active/Resolved/Closed, Area, Iteration, Stack Rank | a dedicated field; closed only when the product owner agrees and acceptance tests pass | Priority 1-3 plus Stack Rank | REPORTED ([Microsoft Learn](https://learn.microsoft.com/en-us/azure/devops/boards/work-items/guidance/agile-process-workflow)) |
| Linear | Initiative > Project > (Milestone) > Issue > Sub-issue | Team, Status, Assignee, Project, Milestone, Cycle, Labels, Priority, Comments | none: free text in the description | Priority (urgent..none) | REPORTED ([Linear conceptual model](https://linear.app/docs/conceptual-model); the page did not mention estimates or sub-issues, from memory they exist) |
| Jira | Initiative > Epic > Story/Task/Bug > Sub-task; Epic Link / Parent | Summary, Description, Priority, Labels, Components, Fix Version, Sprint, Story Points (custom), Acceptance Criteria (custom field or in the description) | custom or in text | Priority scheme | THEORETICAL |
| GitHub Issues + Projects | Issues with sub-issues (hierarchy), issue types, milestones; Projects fields (priority, iteration) | title, body, labels, assignees, type, milestone; project/issue fields | in the body (task-list checkboxes) | a project field | REPORTED, partly ([GitHub docs search](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/best-practices-for-projects)); field details THEORETICAL |
| Shortcut | Objective/Milestone > Epic > Story (Feature/Bug/Chore) | name, description, type, estimate, workflow state, labels, owners | in the description | priority field | THEORETICAL |

### Mapping that the import (`US-import`) carries, as data

| ddflow | Azure DevOps | Linear | Jira | GitHub | Shortcut |
|---|---|---|---|---|---|
| `story` level=epic | Epic / Feature | Project (Initiative = `theme` label) | Epic | issue with sub-issues / milestone | Epic |
| `story` level=story | User Story | Issue | Story | Issue | Story |
| goal / benefit / persona | Description | Description | Description | Body | Description |
| `criterion` | Acceptance Criteria (one per line) | checklist in description | custom field / checklist | task-list items | description checklist |
| MoSCoW | Priority 1/2/3 | Priority | Priority | project field | priority |
| rank in release | Stack Rank | order in project | rank | project order | position |
| `release:X` label | Iteration | Cycle / Milestone | Fix Version / Sprint | milestone | Iteration |
| external id | work item id | identifier | key | number | story id |

Direction (recommended): import first, file-based (JSON, CSV, markdown); export one-way later; ddflow
stays the source of truth for criteria and verification evidence, because no tracker above verifies a
criterion against a test. Two-way live sync is out of scope until the operator decides.

## 7. Recommended design

### 7.1 Model (THEORETICAL; for `US-decide`)

```
story      (new data kind)
  title           one line
  level           epic | story              (theme = a label; no third level in the kind)
  persona         -> stakeholder (class persona)
  goal            what the persona wants
  benefit         "so that ..."
  parent          -> story (epic) | goal (P-goals)
  realises[]      -> requirement ids (product or standard class)
  source[]        operator prompt id | research note | stakeholder | decision   (required)
  moscow          must | should | could | wont
  rank            integer within a release label
  release         label release:X
  state           proposed | draft | ready | accepted | deferred | dropped      (STORED)
  questions[]     open ask ids
  digest_accepted content digest of story + criteria at acceptance
criterion  (new data kind, one per acceptance criterion)
  story           -> story
  form            gherkin | ears | plain
  text            Given/When/Then, "When ... shall ...", or a testable sentence
  verify          test:<pytest node id> | cmd:<command> | pattern | inspection | reviewer
  state           proposed | agreed | verified | stale | dropped
stakeholder (shipped) gains: class (persona|person|group), jobs, pains, context
```

Why a separate `criterion` kind, not an array inside the story: the descriptor supports only
`string`, `array` of strings and `integer` (section 5). An array of encoded strings would need a mini
grammar and would lose per-criterion history, supersession, a `stale`/`verified` lifecycle and the
per-record suspect marking that `PD-trace` works on. Cost: three to seven small records per story;
`story add --criterion` creates them inline, `story show` renders them as one block, and the id shown to
people is `S12#2`. Alternative for the operator: arrays of strings (cheaper, weaker).

Why `story` is not `requirement` with class=story (D-requirements-model chose one kind for the
*engineering* classes): the story lifecycle (ready -> accepted), the parent/epic hierarchy and the owned
criteria differ from a "shall" statement; a kind is data (a TOML file) so the cost of a kind is small;
and `realises` links keep the two layers joined. This is Decision 1 below, with the opposite option kept.

**Derived delivery status, never typed**: unplanned (no linked task) -> planned (tasks filed) -> building
(a task claimed) -> verified (every criterion `verified` at the current tree) ; `accepted` is stored
because it is an act of a person. An epic's percentage is computed from its children.

### 7.2 Workflow

```
operator request (session prompt, verbatim)           <- source
  -> elicitor drafts story + criteria; every gap is a question, never a guess
  -> questions go to the operator in ONE batch, each with a recommended default   (ask engine)
  -> answers stored verbatim; story updated; lint: INVEST + smells               (US-lint)
  -> `story ready S`: Definition of Ready                                         (US-ready)
  -> a person marks it ready/accepted                                             (US-accept)
  -> `story plan S`: skeleton plan, tasks tagged story:S, acceptance = criterion ids
  -> `plan check` (globs, cycles, overlap, every criterion covered) -> apply (PROPOSED) -> operator confirms
  -> claim: the story pack (<= 300 tokens) shows the story and criterion ids        (US-inject)
  -> implement; tests named by the criteria are written first                       (ATDD)
  -> `story verify S`: each criterion's verify run, evidence recorded per criterion (US-verify)
  -> `story done S`: DoD, then a person accepts                                     (US-dod)
```

Stop rules (agents need them): elicitation stops when one pass yields no new rule or question or after
`[stories].max_elicit_rounds` (default 2); a story is split when it exceeds `max_criteria` (default 7) or
`max_tasks` (default 8); `verify` retries are bounded by the pipeline's existing round limits.

### 7.3 Definition of Ready (data: `[stories].ready_checks`)

1. persona resolves to a stakeholder of class persona; 2. goal and benefit non-empty; 3. at least one
criterion; 4. every criterion parses and has a `verify` (or is explicitly `manual` with a named reviewer);
5. no ambiguity word, no implementation word flagged as error; 6. `source` cites something that resolves;
7. no open question; 8. size: criteria and estimated tasks within limits; 9. parent present or
`orphan_ok`; 10. a person accepted (agents only for `requirements.agent_accept_classes`). Errors block,
smells warn; every threshold is a knob (D-universal-principles).

### 7.4 Definition of Done

Every criterion has an outcome, never silence: `verified` (the test or check passed at the current tree
and, by default, did not pass on the tree before the story: the bug-regression rule reused), `unavailable`
(could not run: recorded, never passed), or `waived` (with a reason, through the existing deviation and
waiver engine). All linked tasks done; no suspect link; the docs gate passed; a person accepts.

### 7.5 Hooks into sessions

A *story pack* for the item being worked: story line, persona, benefit, criterion ids with one line each
and their verify state, plus open questions, at claim, in the brief, and in a subagent launch brief.
Budget `[stories].charter_tokens` default 300 (about 1.2 KB), at most two stories, truncated by dropping
the criteria text before the ids; absent for unrelated items. It is the contextual tier of
D-requirements-injection and never enters the always-on charter (500 tokens, MUSTs only). Lean profile
(8-16K start, `docs/ddflow/research/lean-start.md`) is respected because the pack arrives at claim, not at
session start. THEORETICAL budgets, to be measured by `US-e2e` with the `LS-measure` method.

### 7.6 Traceability and orphans

Links: story -realises-> requirement; criterion -of-> story; task -tag story:S, acceptance = ids->
criterion; criterion -verify-> test node id; task -> commit sha (existing). Reports in doctor and brief:
stories with no criterion, criteria with no verify or a failing verify, tasks with a story tag and no
criterion claimed (gold-plating), tasks with no story where `[stories].require_story` is on, stories with
no epic and no source, suspect links after a change. Uses the `PD-trace` primitive.

### 7.7 Change handling

An edit to an accepted story or its criteria stores the new digest, marks linked tasks and `verified`
criteria suspect/stale, tells sessions holding those tasks through `B-rc-changes`, `DX-drift-notice` and
`DX-merge-fanout-notify` at their next door, and raises an ask for the operator: re-plan, keep, or revert.
Nothing is rewritten silently.

### 7.8 Surfaces (tool-diet world)

CLI `ddflow story list|show|add|edit|remove|supersede|split|search|dupes|ready|plan|verify|done|trace|rank|map|import|export|ask`
(one declaration); MCP one tool `ddflow_story` with an `action`, no new top-level tools (SE-mcp-budget, open;
P-tool-diet). `ddflow record story ...` and `ddflow record criterion ...` keep working for free.

### 7.9 Where the operator decides

Accepting a story as ready/accepted (a person, except the low-risk classes); answering clarifying
questions; confirming a plan (`plan confirm`); the priority and release order; waiving a criterion;
raising any autonomy class; sending anything to a tracker; adopting an agent-proposed story (proposals
never queue by themselves, D-goals-principles).

## 8. Defects and surprises (found while researching)

| Id | What |
|---|---|
| `ddflow external` is not a tracker integration | its name suggests it; it only observes sibling-repo `needs`. Documentation gap, no code defect; the phase doc says so |
| MCP `ddflow_task_add` has no `kind` while the CLI has `--kind` | filed as a defect task (section 11) |
| `requirement` and story kinds are not in `[dedupe].kinds` | covered by `RQM-dupes` and `US-dupes` |
| Descriptor fields cannot hold structured objects | design consequence (section 7.1), not a defect |

## 9. Gaps, ranked

| # | Gap | Evidence | Task |
|---|---|---|---|
| 1 | No story record: persona, goal, benefit and criteria are prose | section 3 | `US-model`, `US-criteria` |
| 2 | No clarifying-question surface: the engine exists, nothing exposes it | `B-am-operator-qa` open | `US-elicit` |
| 3 | No readiness check: unrefined work enters the queue; `plan check` tests only the plan shape | `plancheck.py` | `US-lint`, `US-ready` |
| 4 | No criterion -> test evidence; "done" is gate outcomes, not criteria | `verify.py`, `bug_close.py` | `US-verify`, `US-dod` |
| 5 | No trace or orphan detection above the task | queue | `US-trace` |
| 6 | No story pack in sessions | `inject.collect` closed (see requirements doc) | `US-inject` |
| 7 | Plan has no criterion coverage check | `plancheck.py` | `US-plan` |
| 8 | No change handling: an edited story leaves running tasks stale | queue | `US-change` |
| 9 | No tracker import / export | section 3 | `US-import`, `US-export` |
| 10 | No story-level prioritisation or release grouping | `PD-scoring` is per requirement | `US-priority`, `US-views` |
| 11 | No splitting aid | - | `US-split` |
| 12 | Personas not modelled | `stakeholder.toml` | `US-persona` |
| 13 | No evaluation of the failure modes | - | `US-eval` |

## 10. Phase and tasks

Phase `P-user-stories`, 26 tasks including this one, none started except `US-research`. Phase `needs`
(all done): `SE-kind-data`, `B-dt-rules`, `B-rc-participation`. **Hold**: `US-model` and `US-persona`, and
through them every implementation task, also need the docs wave `P-doc-templates`, `P-docs-generic`,
`P-roadmap`, `P-docs-search` (D-old-work-behind-core-docs; `B-gap-log-scale`, `B-gap-lanes`,
`B-gap-plan-lint` are done). `US-research` and `US-decide` are not held: the research and the
operator's choices can proceed.

Waves below are the longest `needs` chain INSIDE the phase (computed by `core.schedule.waves` through
`ddflow plan check`, then re-derived by an independent script over the folded state; no cycle, every
`needs` exists). The external `needs` that are still open (`PD-*`, `RQM-provider`, `B-am-operator-qa`,
`B-rc-changes`, `DX-drift-notice`, `PL-proposed-intake`, `SE-hazard-scan`, `FL-tracker-contract`) can
delay a task past its wave; the column "open external needs" names them.

| Wave | Task | Needs inside the phase | Open external needs |
|---|---|---|---|
| 1 | `US-decide` | `US-research` | - |
| 2 | `US-model` | `US-decide` | `P-doc-templates`, `P-docs-generic`, `P-roadmap`, `P-docs-search` |
| 2 | `US-persona` | `US-decide` | `P-doc-templates`, `P-docs-generic`, `P-roadmap`, `P-docs-search` |
| 3 | `US-accept` | `US-model` | `PD-source` |
| 3 | `US-criteria` | `US-model` | - |
| 3 | `US-dupes` | `US-model` | - |
| 3 | `US-surfaces` | `US-model` | `SE-mcp-budget` |
| 4 | `US-elicit` | `US-surfaces` | `B-am-operator-qa` |
| 4 | `US-import` | `US-surfaces` | `SE-hazard-scan`, `PL-proposed-intake` |
| 4 | `US-lint` | `US-criteria` | `PD-lint` |
| 4 | `US-priority` | `US-surfaces` | `PD-scoring` |
| 4 | `US-verify` | `US-criteria`, `US-surfaces` | - |
| 5 | `US-ready` | `US-lint`, `US-elicit`, `US-accept` | - |
| 5 | `US-split` | `US-criteria`, `US-surfaces`, `US-lint` | - |
| 6 | `US-plan` | `US-ready` | `PL-proposed-intake` |
| 7 | `US-inject` | `US-criteria`, `US-plan` | `RQM-provider` |
| 7 | `US-rollup` | `US-plan`, `US-verify` | - |
| 7 | `US-trace` | `US-plan`, `US-verify` | `PD-trace` |
| 8 | `US-change` | `US-trace`, `US-inject` | `B-rc-changes`, `DX-drift-notice` |
| 8 | `US-dod` | `US-rollup`, `US-accept`, `US-trace` | - |
| 8 | `US-export` | `US-import`, `US-rollup` | `FL-tracker-contract` |
| 8 | `US-views` | `US-rollup` | `PD-doc-types`, `B-dt-library` |
| 9 | `US-e2e` | `US-ready`, `US-plan`, `US-inject`, `US-dod`, `US-change`, `US-priority`, `US-split` | - |
| 9 | `US-eval` | `US-lint`, `US-trace`, `US-change`, `US-import` | - |
| 10 | `US-docs` | `US-e2e`, `US-eval`, `US-views`, `US-export` | - |

Reading the table: waves 1-3 give a usable story record with criteria, CLI/MCP and duplicate check
(small, mostly independent of the open `PD-*` work except `US-lint`); waves 4-6 give questions,
readiness and planning; waves 7-8 give trace, rollup, injection and change handling; wave 9 proves it;
wave 10 documents it. `US-lint`, `US-accept`, `US-trace`, `US-priority`, `US-views` are the tasks most
exposed to P-product-docs timing: if `PD-*` slips, ship the story-only subset of each rule/check first and
adopt the shared engine later.

### Links filed with existing items

`US-persona` related `PD-stakeholders`; `US-dupes` related `RQM-dupes`; `US-plan` related `AP-stories`;
`US-trace` related `PD-trace`, `RQM-trace`, `TF-trace`; `US-inject` related `RQM-inject`, `RQM-subagent`,
`LS-launch-brief-budget`; `US-import` related `SM-gh-import`, `EXT-clickup-intake`, `SDD-import`;
`US-export` related `FL-receipt`; `US-model` related `PD-req-kind`, `SX-acceptance-field`; `US-ready`
related `RQ-ready-verdict`; `US-priority` related `PD-scoring`; `US-views` related `PD-doc-types`.
Overlap with `P-specs`: criteria live on the story; a spec's acceptance suite should *cite* criterion ids
(see `SPEC-research` once it lands); no task of this phase writes `ddflow/services/specs/**`.

## 11. Defects filed while doing this research

| Id | Fix task | What |
|---|---|---|
| Bmcp-task-add-no-kind | fix-Bmcp-task-add-no-kind | MCP `ddflow_task_add` has no `kind`; CLI `task add --kind task|doc|research|job` does (low). The fix task's globs are a guess; confirm at claim |

Surprises that are not defects: `ddflow external` is sibling-repo sync only; `SE-mcp-budget` and `SE-globs`
are still open (an earlier note called the former done), so `US-surfaces` waits for the budget ratchet and
`US-model` deliberately does not need `SE-globs`.

## 12. UNKNOWN

- Whether a third hierarchy level (theme or initiative) is wanted beyond a label: no operator statement.
- The full INCOSE C1-C14 list and the ISO/IEC/IEEE 29148 text: not opened; only secondary summaries.
- Jira, Shortcut and GitHub field details beyond the documented hierarchy: from memory or a thin search
  result; `US-import`'s mapping file must be checked against each product's export before it ships.
- Whether `RQM-provider` will accept a second record kind: the task text says "any data kind with scope
  fields", unverified until it lands.
- Where `PD-lint` will keep its rule data (`US-lint` guesses `ddflow/templates/lint/stories.toml`; align
  at claim time).
- The exact shape of `P-specs`' spec record and its acceptance field: `SPEC-research` was running.
- Token cost of the story pack: 300 tokens is a budget, not a measurement.
- Whether operators will tolerate three to seven small criterion records per story: a usability question
  for `US-decide`.
- Timing of `P-product-docs` and `P-roadmap`: not scheduled; they gate several tasks.
