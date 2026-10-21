# Managing engineering requirements and standards: what ddflow has, what is planned, what is missing

Task `RQM-research` (phase `P-requirements`). Written 2026-10-21 by agent `res-requirements`.

Operator question (verbatim, also recorded with `ddflow session prompt`): "can you check if we can
manage project requirements (as the rest of the documents, both as documentation, but also both as
ability to insert the requirements brief in the sessions and contextually where applicable, with the
respective check in the agent sessions, the same as managing rules, policies, goals, exposing
requirements to the user to manage with all the abilities to edit, delete, list, duplication checks,
etc) - as for example to manage requirements of how the software to build - aka use cmake, conan, this
type of format of cmake, etc. Such requirements and standards should be possible to be checked and
received both from external tools (codeguide-mcp or other mcp servers), corporate policies (remote,
local), or per project requirements, user managed imposed, or stored as requirements doc files, managed
by ddflow. Check if we have these features, or we plan to implement them, what of the internal cores we
could reuse, how to make the rules, requirements, goal, policies, documentation, skills, subagents, etc
- to be instructing periodically the agent sessions, are we having gaps, how to improve on that. Do a
deep research, and propose phase + tasks for corrections if any"

## Evidence tags

| Tag | Meaning |
|---|---|
| CONFIRMED | read in the code at the cited file:line, or a command was run and its output is quoted |
| REFUTED | the claim was tested and is false |
| THEORETICAL | a design, a survey or a source not opened |

Measurements are bytes, tokens estimated as bytes / 4. Sub-agent findings were re-checked by the
author before use; each CONFIRMED below names the probe. Research notes: `R-req-*` in the log.

## 1. The short answer

1. **A `requirement` record kind already exists and is inert.** `ddflow/templates/kinds/requirement.toml`
   declares title, statement, rationale, state (proposed -> approved -> implemented -> verified),
   priority (must/should/could), source and stakeholders. `ddflow record requirement list` works
   ("No requirement records."). It has no scope (globs, contexts), no verification, no layer or
   owner, and nothing delivers it to a session: `RecordKind.applies` is declared and unused
   (`ddflow/api/records.py`, `_check_declarations`), and `inject.collect` returns live rules and
   decisions only (`ddflow/services/guidance/inject.py:104`). CONFIRMED.
2. **Product requirements are planned, engineering requirements are not.** Phase `P-product-docs`
   (task `PD-req-kind`: EARS text, source, stakeholders, baselines, person-only acceptance) plans the
   *product* requirement pipeline. Nothing in the queue plans "use CMake >= 3.25 / Conan 2 / no
   in-source builds" as a scoped, injected, verified record. CONFIRMED by grep over 1,000 tasks and
   the decision documents.
3. **Most of the machinery exists or is planned elsewhere**, so the new phase is thin: the record
   surface (done), data kinds (done), guidance scoping/ranking/provenance/dedupe (done), a checks and
   waivers engine (built, unwired), doc types with per-document guidance (done, B-dt-rules), org
   layers (planned, P-org-config), pipeline requirements (planned, B-pipe-*), change feed and
   relevance context (planned, B-rc-changes, B-rc-context), delivery evidence (planned,
   P-safeguards.delivery).
4. **The real gaps are in delivery, not storage.** On this repo, today, a fresh session receives none
   of the 7 project rules (brief prints "7 more cut to fit session.brief_max_tokens"), the claim block
   shows 1 of 57 applying records, the launch brief for a subagent carries no rules, the
   UserPromptSubmit hook injects nothing, and nothing records that anything reached the model.

## 2. Inventory: capability matrix

Legend: Y = CONFIRMED present; N = CONFIRMED absent; P = partial; plan = only in the queue.
Columns are abbreviated: scope = glob/context scoping, reinj = re-injection after session start,
enf = enforcement or check, ext = external source, imp/exp = import / export, MCP = MCP exposure.

| Kind | store | list/show | add | edit | remove | supersede | dupe/conflict | scope | reinj | enf | ext | imp/exp | MCP | CLI/MCP parity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | log `def.*`, files are a view (`api/rules.py:328`) | Y (`rules.py:777,819`) | Y (`:529`) | Y (`:693`) | Y retire (`:749`) | **N** | Y Jaccard 0.55 + cross-kind (`rules.py:62,165`) | Y globs/categories/gates | P claim, brief, gate, review; **cut in practice** | P ranking only | N | `rule sync` files; export RULES.md; import N | Y 7 tools | Y |
| decisions | log events | Y list/show/applicable | Y | **N** | **N** | Y (`api/decisions.py:152`) | Y + `unreconciled` pairs | Y globs | same doors | P warn by default | N | import ADR dirs; export | Y + resource | P (no search tool) |
| lessons | log events | list/search; **no show** | Y | **N** | **N** | P (`--supersedes`) | Y | P (`--globs` scans a pattern) | brief only | P `lesson verify` | N | import; summary file | Y | P |
| memory | log events | list; no show | Y | P re-add | Y forget | N | Y | N | brief only | N | N | import | Y | Y |
| goals / principles | plan (B-goal-model) | plan | plan | plan | plan | plan | plan | plan | plan | plan | N | N | N | N |
| policies | N (`[enforce]` knobs only; B-org-policy, B-org-agent-guard planned) | N | N | N | N | N | N | N | N | knobs block/warn/off | N | N | N | N |
| requirements (kind) | log `def.*` via data kind | Y `record requirement list/show` | Y | Y | Y retire | Y generic record verbs | Y add-time check | **N** | **N** | **N** | N | N | resource if enabled | CLI only via `record` |
| doc types | TOML files only (`templates/doctypes`, `.ddflow/doctypes`), not in the log | **N** (no `docs types`) | hand edit | hand edit | hand edit | `extends` | N | Y per-type glob | via B-dt-rules guidance (`doctypes/guidance.py`) | P `docs check` validate rules | local sources only | export templates | only `ddflow_docs check` | n/a |
| skills | files in harness dirs | inventory in brief (`services/skills.py:129`) | **N** (ddflow never writes) | N | N | N | N | BM25 rank only | brief one line each | N | N | N | via brief | n/a |
| subagent defs | files in harness dirs; ddflow reads none | N | N | N | N | N | N | N | one-shot launch brief (`services/agent_brief.py`) | N | N | N | via brief `for_agent` | n/a |
| harness files | managed block in AGENTS.md/CLAUDE.md + 6 native files (`services/adopt.py` NATIVE_RULES) | `harness list/show/doctor` | `adopt` / `setup` | refresh | N | digest stamp | N | none (always-on) | once | P pre-commit drift | N | upgrade plan | `ddflow_setup` | Y |
| companions | `templates/companions.toml` | `companions` | `companions add` | N | N | N | N | gate list | none | recorded `unavailable` | **ddflow does not call them** | N | Y | Y |
| standards gate | pipeline gate in `services/gates/defs.py:205` | gate status | n/a | n/a | n/a | n/a | n/a | gate | per gate status | agent-run gate, reviewer | codeguide only as prose (`mcp_instructions.md:158`) | n/a | Y | Y |

Evidence for the cells that matter:

- Rules/decisions/lessons verbs: `ddflow rule --help` lists `add,edit,list,search,show,remove,sync`
  (no supersede); `ddflow lesson --help` lists `add,verify,search,list`; `ddflow docs --help` lists only
  `check`. CONFIRMED by running them.
- Guidance data model: `GuidanceRecord` (`services/guidance/record.py:92-127`): id, kind, title, body,
  level, category, tags, `Scope(globs, categories, gates)`, `priority`, `enforcement`
  (advisory/warn/block), `status`, `review_by`, owner, sources, `checks`, links, provenance, `ext`.
  Adapters exist for rules (`deffields.from_fields`) and decisions (`kinds.decision_record`); document
  types feed extra records through `inject._document_types`. CONFIRMED.
- Checks and waivers are dead plumbing: `grep -rn "run_record|gate_verdict|checks.register|waivers.apply"`
  outside `services/guidance/` returns only an unrelated string in `api/setup.py:1122`; no check kind is
  registered. CONFIRMED. `waivers.py` has the 90-day, glob-scoped, person-approved-by-digest model;
  storage waits for B-dec-waivers.
- Reinstruct footer: `config_sections/reinstruct.py` (every 12 calls and 240 s, max 3 items) lists
  *obligations* (open bugs, skipped gates, no lessons, rules-file drift; `services/obligations.py`),
  never rule or requirement text. CONFIRMED.
- Harness files: only AGENTS.md/CLAUDE.md and `.cursor/rules/ddflow.mdc`, `QWEN.md`,
  `.clinerules/ddflow.md`, `.tabnine/guidelines/ddflow.md`, `replit.md`, `.goosehints`, aider `read:`
  are written. No `GEMINI.md`, no `.github/copilot-instructions.md`, no `.github/instructions/*.md`
  (applyTo), no `.claude/rules/*.md`. CONFIRMED (`services/adopt.py`, `harnesses/*.toml`).
- Companions: `companions.verify_one` (`services/companions.py:452-474`) sends `initialize` and nothing
  else; there is no `tools/list`, `resources/read` or `prompts/get` client call anywhere in `ddflow/`.
  CONFIRMED by grep. The "standards" use of codeguide is one sentence for the agent in
  `templates/prompts/mcp_instructions.md:158`.

## 3. What is planned (so nothing is duplicated)

| Planned item | Covers | Does not cover |
|---|---|---|
| `P-product-docs` (`PD-req-kind`, `PD-lint`, `PD-trace`, `PD-baseline`, `PD-source`, `PD-stakeholders`) | product requirements: EARS text, mandatory source, person acceptance, suspect links, baselines | engineering standards, scoping/injection, verification commands |
| `P-goals` (`B-goal-deviation`, `B-goal-model`, `B-goal-inject`) | MUST/SHOULD/MAY as enforcement x override, principles, goals in the brief | requirements, external sources |
| `P-record-contract` (`B-rc-changes`, `B-rc-context`, `B-rc-lifecycle-guard`) | change feed to running sessions, relevance-matched context at prompt/pre-edit/claim with a shown-ledger | any requirement kind; nothing built yet (both open) |
| `B-pipe-*` (`D-pipeline-requirements`) | a guidance record may require a gate in the resolved pipeline (`pipeline` field) | verification of the requirement itself |
| `P-org-config` (`B-org-layers`, `B-org-policy`, `B-org-sources`, `B-org-trust`, `B-org-lock`) | layered config incl. enforced org layer, signed/pinned sources, lock file | requirements as a syncable kind (B-org-kinds lists rules, prompts, macros...) |
| `P-safeguards` (`.delivery`, `.consult`, `.style-cards`, `.cards-client`) | delivery evidence, consult-before-write action, style cards from tool configs, research on ddflow as MCP client | requirement text and scope |
| `P-context-sync` | tell/ack/stale markers between lanes | requirements |
| `P-lessons`, `P-lesson-packs` | lessons delivered by glob, promoted to checks, shipped packs | requirements |
| `OB-rules-glob`, `OB-emit-manifest`, `OB-canonical-agents` | emit of path-scoped rules to Cursor .mdc, Copilot applyTo, Claude paths, Cline, Windsurf; AGENTS.md canonical; digest manifest | requirement records as a source |
| `KB-*` (P-knowledge-hub), `CN-req-import`, `SDD-rules-bridge` | cross-project policy bundles with pinning and promotion; Doorstop/StrictDoc/sphinx-needs read-only import; steering/constitution <-> rules | engineering requirement scope, verify, injection |
| `P-change-surface.context-budget` | measuring and trimming the always-loaded context | a charter tier inside it |
| `RQ-check-record/run` | `check` record kind evaluated through reviewers | project requirements |
| `SC-style-codeguide`, `CG-codeguide-remote-default` | choose codeguide languages; public remote server | ddflow calling it |
| `P-skills`, `P-subagents` | skills/agent definitions as managed records and sync | requirement cross-references |
| `P-iac` `IA-policy` | policy-as-code (OPA/Conftest) bundles for infrastructure | project engineering standards |

`P-product-docs` is held behind the core and docs wave (D-old-work-behind-core-docs) like `P-goals`.

Queue hygiene note: `ddflow list task --all --limit 5000` is silently capped at 1000 rows (2,670
tasks exist), so this inventory was sliced by state x `--since` (2,121 tasks read; the older done ones
cannot concern a requirement plan). Bug filed (see section 11).

## 4. Use cases to design against

All use one record. `[c]` = contexts, `V` = verify method.

| Requirement | strength | scope | injected when | V | reported as |
|---|---|---|---|---|---|
| Build with CMake >= 3.25 (`cmake_minimum_required`) | must | `CMakeLists.txt`, `*.cmake`, `[c]=cmake` | claim/pre-edit on those files | `config`: assertion on the VERSION field; `command`: `cmake --version` compared | requirement id + result in gate record |
| Dependencies through Conan 2, `conan.lock` committed | must | `conanfile.*`, `CMakeLists.txt` | on `conanfile*`/`find_package` edits | `file` exists + `pattern` no `FetchContent` of release deps | same |
| Use CMakePresets.json, no in-source builds | must | `[c]=cmake` | claim | `file` + `command`: `cmake --preset ... -S . -B .` refuses in-source | same |
| Warnings as errors: `-Wall -Wextra -Werror` | should | `[c]=cpp` | claim for C++ items | `pattern` over presets/toolchain | deviation needs a reason |
| clang-format / .editorconfig style | should | `*.cpp,*.h` | pre-edit | `command`: `clang-format --dry-run --Werror` on changed files | lint output as evidence |
| Licence headers (SPDX) | must | `src/**` | claim | `pattern` first lines | gate |
| No secrets, signed commits, dependency licence allowlist | must | all | charter | `command`: gitleaks companion (RQ-toolctx-secrets) | gate |
| Branching/PR: trunk, PR required, item trailer | must | repo | charter | `[enforce]` knobs exist (`require_item_trailer`), map to `verify.kind=config` | doctor |
| Test policy: regression test for every bug | must | bugs | claim of a bug | already enforced (rule 4 of CLAUDE.md) | existing gates |
| Documentation policy: README updated with a new command | should | `ddflow/surfaces/**` | claim | existing docs gate + `docs check` | existing |

Mechanical checks (1-4 of the verify kinds) never need a model. Natural-language requirements
(`reviewer` kind) go to a reviewer gate with the id in the prompt and a cite-or-not-applicable
contract.

## 5. External sources and layering

Today: no remote fetch anywhere. `ddflow import` is local files (no `http`/git/MCP code in
`services/importer*.py`); `ddflow external` reads a sibling repo's local path
(`services/external.py`); doctype sources are `file:`, `cmd:`, `kind:`, `query:`, `fragments:`,
`registry:` (all local). `ddflow/services/org` does not exist (CONFIRMED by `ls`), there is no
`[guidance]`, `[org]` or `[policy]` config section. Trust vocabulary exists: operator | agent |
imported | unknown (`core/provenance.py`), `Origin.source` is reserved for a URL.

### Recommended layering (THEORETICAL; for D-decide)

```
org enforced  >  org defaults  >  team  >  project  >  local (user/machine)  >  task
(highest)                                                          (lowest, narrowest)
```

- A requirement is identified by id (namespaced by source: `org/...`, `codeguide/...`, `proj/...`).
- A lower layer may only **tighten** (raise strength, narrow scope, add a verify). Loosening an org
  floor is a conflict, reported and ignored, unless a person records a scoped, reasoned, expiring
  waiver (the waivers module, 90 days). This is `B-org-layers`/D-config-layer-order applied to a kind,
  not a new precedence.
- Freshness (from OPA bundles, opened): conditional GET with ETag, verify before activation, keep the
  last good version on failure, pin by digest, offline mode uses the lock. OPA gives no overlap check
  between bundles, so ddflow must detect cross-source id/topic overlap itself.
- Trust: remote records arrive `trust=imported`, fenced as DATA (`<ddflow-record trust=...>`); an
  unknown/imported source may PROPOSE and may not apply a MUST; a person accepts (the D-product-docs
  pattern).
- External MCP (codeguide and others): ddflow needs a minimal MCP *client* (resources/list,
  resources/read, prompts/get). Today it has none (section 2). A guide becomes a `class=standard`
  requirement card with pinned digest, `strength=should`, refreshed on an interval. Only the guide name
  leaves the machine. Public remote server per `CG-codeguide-remote-default`.
- Conflict detection: same `topic` with different `expect` across layers; project loosening an org
  floor; external card vs project MUST; requirement vs live decision on overlapping globs (reuse
  `guidance.kinds.unreconciled`); disagreeing verify commands. A conflict sets `contested`, which
  already removes a record from injection (`GuidanceRecord.live`).

## 6. Periodic instructing: what happens today, what it costs

Measured on this repo (commands quoted in the R-req-inject-timeline note):

| Door | Trigger | Content | Repeats | Size |
|---|---|---|---|---|
| SessionStart hook (`ddflow hooks run session_start`) | startup, resume, clear, compact (`services/claudehooks.py:44`) | header, compaction digest, drift, cadence, full brief | per start/compact | 5,374 B (~1,343 tok); compact 5,661 B |
| UserPromptSubmit hook | every prompt | records the prompt, returns nothing (`api/setup.py:967-975`) | every prompt | **0 B** |
| PreCompact | before compaction | writes a digest note; cannot inject (`api/setup.py:977-990`) | per compaction | 0 B |
| PostCompact, Stop, PostToolUse | - | no handler (`api/setup.py:1021-1028`) | - | - |
| PreToolUse Bash | each Bash call | denies forbidden commands only | each | 0 B |
| `ddflow brief` | agent calls | queue, memory, rules block, lessons, skills; cap `session.brief_max_tokens`=1200 | on demand | 4,208 B |
| Claim guidance | `ddflow_claim` | `GI.for_work`, door budget 25% of 1200 = 300 tok | per claim | ~1.2 KB; 1 of 57 applying records shown |
| Gate status | every call | gate template + guidance for the gate | each call | ~1.5 KB |
| Subagent launch brief | coordinator pastes | 13-section template + up to 6 decisions, 3 lessons; no rules | once | 8,787 B (cap 9000) |
| MCP initialize instructions | connect | `mcp_instructions.md` + dynamic blocks; pointers, no rule text | once | 12,718 chars (~3.2k tok) |
| MCP tools/list | connect | 123 tools | once (cached) | 108,733 B |
| Obligations footer | tool results | up to 3 undone obligations | at most every 12 calls and 240 s | small |

Consequences (all CONFIRMED unless marked):

- **Rules are cut entirely.** `ddflow brief` prints `## Project rules - [7 more cut to fit
  session.brief_max_tokens ... the rules file]`. At claim, ranking is block > warn > advisory, all 7
  rules are advisory, the repo holds 263 decisions, so the claim block for B-dt-rules shows 1 of 57
  records (a warn decision). The "pinned" exception (`inject.pinned`: always-scope + block) exists but
  no rule is marked so.
- **Compaction.** The `compact` SessionStart hook re-sends the brief (rules still cut). Claude Code
  itself re-injects unscoped CLAUDE.md after compaction but path-scoped rules only on the next matching
  file access (code.claude.com/docs/en/memory, opened). So the always-on charter must be unscoped.
- **Subagents** get rules only by reading CLAUDE.md themselves (whether they do is UNVERIFIED); the
  `SubagentStart` event has no ddflow handler.
- **Harnesses without a delivering hook:** aider (none), glm, qodo, replit, cody (stubs); antigravity,
  cline and windsurf list `inject=["prompt"]` but the prompt door emits nothing; no MCP at all for
  aider, cline, windsurf, replit, openhands, goose, cody. They rely on the instruction file.
- **Mid-session changes** reach a running agent through no channel: ddflow advertises
  `listChanged:false` for tools, prompts and resources; per `docs/ddflow/research/mcp-client-matrix.md`
  only `tools/list_changed` is honoured (Claude Code, Kilo VERIFIED) and `instructions` is read once at
  `initialize`. `B-rc-changes` is the planned fix.
- **No acknowledgement or compliance measure.** No read-receipt or per-delivery record exists
  (`P-safeguards.delivery` planned). A read-receipt for text injected into a model is impossible; the
  honest design is three proofs: delivered (door + digest), applied (the agent's per-requirement
  statement at the gate), and checked (a mechanical result), compared.
- **Cost of a per-prompt reminder:** titles only for 7 rules ~220 tok/prompt (~22k over 100 prompts);
  full bodies 2.3-3k tok/prompt (230-300k over 100) - unaffordable, hence a shown-ledger by digest
  (`B-rc-context`) and a digest line on a cadence, reset on compaction (Vectara reminders:
  skip_first / fire_every, opened).

### Recommended injection tiers (THEORETICAL, budgets are defaults)

| Tier | What | When | Budget |
|---|---|---|---|
| 0 charter | one line per MUST: id, <= 12 words | SessionStart incl. compact, MCP initialize, instruction file (unscoped), subagent launch brief | 250 tok, never trimmed, replaces the "N more cut" line |
| 1 contextual | statement + verify command of requirements matching item globs, contexts, gate | claim, pre-edit on a matching path, gate status, review prompt | within the existing 300-tok door budget, minimum share per kind |
| 2 on demand | search | `ddflow requirement for`, `ddflow_recall` | per call |
| 3 digest | "N MUST apply to files you are editing: ids" | every K prompts / S seconds, only if the set changed or was last shown > K prompts ago, reset on compaction | 60 tok |
| 4 change | "requirement X changed (BINDING)" | next door after the change (B-rc-changes) | 80 tok |

## 7. Other tools (what changes the design)

Sources opened by the research sub-agent; THEORETICAL unless a URL is cited.

- Cursor rules (cursor.com/docs/context/rules): four inclusion modes, always / globs / description /
  manual, plus enforced Team rules. Lesson: one explicit `inclusion` field, an enforced org tier.
- Copilot (docs.github.com ... add-repository-instructions): `.github/instructions/*.instructions.md`
  with `applyTo`; `excludeAgent` per consumer; conflicting files are all delivered. Lesson: scope by
  consumer (agent vs reviewer) as well as path; detect conflicts, do not rely on precedence.
- Claude Code (code.claude.com/docs/en/memory, context-window): CLAUDE.md is context, not enforcement;
  `.claude/rules` with `paths:`; unscoped rules survive compaction, path-scoped ones are summarised away;
  hooks (`InstructionsLoaded`) can log what was loaded. Lesson: the charter is unscoped; enforcement
  lives in hooks and gates; log delivery.
- AGENTS.md (agents.md): nearest file wins; no structure. Lesson: a compile target, never the source.
- Kiro steering (kiro.dev/docs/steering): always / fileMatch / manual / auto; dangling file references
  show a visible marker; custom agents do not load steering unless told. Lesson: subagents need rules
  delivered explicitly; unresolved references must be loud.
- spec-kit constitution (github/spec-kit spec-driven.md): articles with gate questions; a failed gate
  needs a justification record; amendments are dated with rationale. Lesson: waiver with reason as a
  first-class record (ddflow has `gate skip --reason`), amendment history on the record.
- OpenSpec: current-truth specs plus change deltas merged on archive; same split as ddflow's rules vs
  tasks.
- Doorstop / StrictDoc / Sphinx-needs / ReqIF (opened shallowly; suspect-link details THEORETICAL):
  stable UID, links across documents, validation, ReqIF as an export adapter only. Lesson: one file or
  record per requirement, suspect links (PD-trace).
- OPA bundles (openpolicyagent.org/docs/management-bundles): ETag polling, signed bundles, roots per
  bundle, last-good fallback, no cross-bundle overlap check. Conftest: deny / warn severities.
- EditorConfig, clang-tidy: root marker, nearest wins, `unset`; `--dump-config` effective config,
  scoped suppressions, an error for an orphaned suppression. Lesson: an "effective requirements for this
  path" command, waivers with an inline scope, orphaned waivers flagged. Conan-center hooks: OK /
  WARNING / ERROR with a configurable threshold; archived, a warning against hand-rolled checkers tied
  to one tool version.
- MCP prompts are user-controlled (spec 2025-06-18 prompts), so rule delivery must ride tool results,
  hooks and instruction files, not prompts.
- Reminder research: event-driven, escalating reminders injected at the point of decision
  (agentpatterns.ai event-driven-system-reminders; docs.vectara.com/docs/agents/reminders); no
  controlled study of reminder strategies was found.
- codeguide-mcp: only a directory listing (glama.ai) was opened: serves guides from GitHub repos as MCP
  resources with caching and fallback. Its tool/resource list was not read: UNKNOWN.

## 8. Recommended design

**One record, no second engine.** The shipped `requirement` kind, extended with:

```
class      product | constraint | standard | policy
strength   must | should | may                 (maps to enforcement x override of P-goals)
globs[]    path scope            contexts[]   named matchers (cmake, conan, cpp ...)
gates[]    gate scope            topic, expect   conflict key (build.system = cmake)
verify     {kind: command|pattern|file|config|reviewer|mcp, spec, expect, timeout}
source     {layer, uri, rev, digest, fetched_at}   owner, version, supersedes, review_by
```

`class=product` records additionally carry the EARS/stakeholder/baseline fields of `PD-req-kind`.
Engineering classes are *adapted* into `GuidanceRecord` (the seam `inject.collect` lacks), so scoping,
ranking, budget, provenance fencing and the claim/brief/gate/review doors work with no per-kind
injector; `B-rc-context` later replaces the call site with its provider registry.

**Compliance.** `ddflow requirement check` runs each verify (command through `services/cmdrunner.py`,
pattern/file/config checks in-process), emits `requirement.checked` with evidence and digests;
unrunnable = `unavailable`, never passed; agent-written or imported commands are inert until approved
by digest. A `requirements` gate is added to the resolved pipeline by `pipeline.require` when a MUST
matches the declared globs or the actual diff; pre-commit runs the cheap subset. `requirement.delivered`
events (ids, digests, door) and the agent's `applied | not_applicable(reason) | violated` line per MUST
give delivered / applied / checked, and a mismatch (applied but check failed) is flagged.

**Surfaces.** `ddflow requirement list|show|add|edit|remove|supersede|search|dupes|conflicts|for|check|
trace|sync|sources|report`, one MCP tool `ddflow_requirement` with `action` (tool-diet), parity test,
README section and help topic generated from the registry, `docs/requirements` front-matter files
synced like rule files, a `requirements` document type and export.

## 9. Gaps, ranked

| # | Gap | Evidence | Task |
|---|---|---|---|
| 1 | Rules and advisory guidance are cut entirely from brief and claim; no pinned charter | brief output; claim computation (R-req-inject-timeline) | RQM-gap-rule-budget, RQM-inject |
| 2 | No requirement scope/strength/verify; kind inert; `applies` unused; `inject.collect` closed | `requirement.toml`; `inject.py:104` | RQM-model, RQM-provider, RQM-scope |
| 3 | No compliance check; checks/waivers engine has no caller | grep | RQM-check, RQM-gate, RQM-strength |
| 4 | No periodic or post-compaction re-instruction; prompt door silent; no PostCompact/SubagentStart | `api/setup.py:967-1028` | RQM-doors |
| 5 | Subagent launch brief has no rules or requirements | `api/briefing.py` | RQM-subagent |
| 6 | No delivery or application evidence | grep; planned only P-safeguards.delivery | RQM-ack |
| 7 | No external source, no MCP client, codeguide is prose only | `companions.py:452-474`, `mcp_instructions.md:158` | RQM-sources, RQM-mcp-client |
| 8 | No org/team layer, no precedence | `ls ddflow/services/org` fails | RQM-layers (needs B-org-layers) |
| 9 | Conflict detection only between decisions | `guidance/kinds.py:104-146` | RQM-dupes, RQM-conflicts |
| 10 | Instruction files: no GEMINI.md, Copilot or `.claude/rules` output | `services/adopt.py` | RQM-harness-files |
| 11 | Rule has no supersede; doc types have no list/show; doc-type rule ids positional | CLI help; `doctypes/guidance.py` | RQM-gap-rule-supersede |
| 12 | Skills and subagent definitions are inventory only | `services/skills.py` | RQM-gap-doc-guidance-log (pointer) and P-skills / P-subagents |
| 13 | No traceability requirement -> task -> gate -> test | ledger stores item text digest only | RQM-trace |
| 14 | Mid-session change not delivered (listChanged false) | mcp-client-matrix.md | B-rc-changes (existing), RQM-doors |

## 10. Phase and tasks

Phase `P-requirements`, 26 tasks, none started. Phase `needs`: `SE-kind-data`, `B-dt-rules`,
`B-rc-participation` (all done). Tasks that depend on open work say so; the first six depend only on
done work or on each other.

| Task | Needs | One-line scope |
|---|---|---|
| RQM-research | - | this document and the R-req-* notes |
| RQM-decide | RQM-research | operator decisions (one kind with a class, strengths, layers, acceptance, tiers, trust) |
| RQM-model | RQM-decide | extend `requirement.toml`: class, strength, scope, contexts, topic/expect, verify, source |
| RQM-surfaces | RQM-model | `ddflow requirement` verbs + one MCP tool with `action`, parity test |
| RQM-dupes | RQM-model | add-time duplicate and same-topic conflict check |
| RQM-scope | RQM-model | `requirement for`, contexts as data, effective set with reasons |
| RQM-provider | RQM-model | descriptor-driven adapter into the guidance engine (the seam `inject.collect` lacks) |
| RQM-inject | RQM-provider, RQM-scope, fix-B91e1d67c87 | tiered injection: charter, contextual, on demand |
| RQM-subagent | RQM-inject | launch briefs carry requirements and rules; SubagentStart |
| RQM-doors | RQM-inject, B-rc-context, B-rc-changes | prompt / pre-edit / post-compaction doors, digest reminder, footer obligation |
| RQM-check | RQM-model | `requirement check`: verify kinds, evidence events, wires the unwired checks engine |
| RQM-gate | RQM-check, B-pipe-model, B-pipe-resolve | `requirements` gate via `pipeline.require`, pre-commit subset |
| RQM-reviewer | RQM-inject, RQM-check | NL requirements in the standards/review prompts, cite or not-applicable |
| RQM-ack | RQM-inject | delivered / applied / checked evidence and `requirement coverage` |
| RQM-strength | RQM-model, B-goal-deviation, B-dec-waivers | MUST/SHOULD/MAY, tighten-only, waivers |
| RQM-docs-sync | RQM-model | requirement files <-> records, doc type, export |
| RQM-layers | RQM-model, RQM-decide, B-org-layers, B-org-kinds | org > team > project > local > task, tighten-only, provenance |
| RQM-sources | RQM-layers, B-org-sources, B-org-trust, B-org-lock, SE-hazard-scan | file/url/git/mcp adapters, pin, ETag, last-good, trust=imported |
| RQM-mcp-client | RQM-sources, P-safeguards.cards-client, CG-codeguide-remote-default | minimal MCP client; codeguide guides as requirement cards |
| RQM-conflicts | RQM-dupes, RQM-layers | cross-source conflicts in doctor, brief, `requirement conflicts` |
| RQM-trace | RQM-check | requirement to task to gate to test, suspect marking |
| RQM-harness-files | RQM-scope, OB-rules-glob, OB-emit-manifest | requirements as a source for the OB emitter; doctor drift |
| RQM-report | RQM-check, RQM-ack, RQM-conflicts | `requirement report`, metrics, README, help |
| RQM-gap-rule-supersede | RQM-research | rule supersede, doc-type list/show, stable doc-type rule ids |
| RQM-gap-doc-guidance-log | RQM-inject | requirement-named skill and role pointers in the contextual pack |
| RQM-e2e | RQM-gate, RQM-doors, RQM-sources, RQM-report, RQM-subagent | CMake/Conan transcript test with seeded faults |
| RQM-docs | RQM-e2e | README, help, drivers, example pack |

## 11. Defects filed while doing this research

| Id | Fix task | What |
|---|---|---|
| B91e1d67c87 | fix-B91e1d67c87 | project rules never reach a fresh session; claim shows 1 of 57 records (high) |
| Bf4c3b12455 | fix-Bf4c3b12455 | `ddflow list --limit` silently capped at 1000; `--limit 0` refused; no paging |
| B1878cc0da3 | fix-B1878cc0da3 | harness descriptors declare `inject=[prompt]` for a door that injects nothing |
| Bbf3e91d5f1 | fix-Bbf3e91d5f1 | `ddflow hooks run` has no dry-run; probing it appended an event to the committed log |

## 12. UNKNOWN

- What codeguide-mcp exposes (tool/resource/prompt names): the repo and its README were not read. The
  first step of `RQM-mcp-client` is to read it.
- Whether Claude Code subagents load CLAUDE.md or fire SessionStart; whether other harnesses honour
  `additionalContext` on `UserPromptSubmit` (the matrix only covers `tools/list_changed`).
- Whether the 25%-of-1200-token claim door is too small for a realistic requirement set; needs the
  eval in `RQM-e2e`.
- Doorstop suspect-link semantics and OpenSpec MODIFIED/REMOVED semantics (pages not opened).
- Token cost of a reviewer-evaluated natural-language requirement per gate (no measurement).
