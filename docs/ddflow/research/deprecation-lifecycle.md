# Obsolescence, deprecation and decommissioning: research

Research item `DEP-research`, 2026-10-22. Operator, verbatim: "As we are discussing this and it happens often - can we research and think about how ddflow can manage for projects obsolence, decommissioning of features, keeping for how long they should exist, notifying the customers and users (including via documentation or alert) about the changes? Do a research for the best practices and make a proposal - see if it duplicates existing features, or could extend them, etc".

Proposal (operator decides): `docs/ddflow/decisions/deprecation-lifecycle.md`. Phase `P-deprecation` (prefix `DEP-`).
Evidence tags: FETCHED = read on the named page in this session; UNVERIFIED = from memory, not read.

## 1. Question and short answer

Can ddflow manage the whole life of a thing that is going away (a knob, command, MCP tool, event kind, doc, or a project's own feature): how long it lives, who is told, how, and what stops a premature removal? Short answer: ddflow already has most of the machinery for ITS OWN renames, but it is spread over seven places, keyed to code constants, and has no record of the decision, no end-of-window enforcement, no usage evidence and no path for a managed project's features. One managed record kind (`deprecation`) plus thin readers in the existing surfaces closes that, with no new MCP tool.

## 2. What ddflow already has (inventory)

| Capability | Where | What it does today | Gap for deprecation |
|---|---|---|---|
| Alias for renamed CLI command/flag, MCP tool/argument | `surfaces/registry.py` `Alias(kind, old, new, since, removed_in)`, `Notices` | old name works, hidden from help and `tools/list`, one notice per process/session | `removed_in` is only floor-checked (`>= 1.0`); nothing removes anything at that version; no replacement text, severity or audience |
| Renamed config keys | `config.py` `RENAMED` (`config_sections/_docs.py`), `migrate_renamed` | old key read, one stderr warning per process, next write moves it | same: warning only on stderr, in a table in code |
| Compat floor | `config_sections/_compat.py` `MIN_REMOVED_IN = "1.0"`, `check_rename` | refuses a declaration that removes before 1.0 (D-compat-r2) | conflicts with the 2-release windows in D-mcp-tool-families and Q12 of CM-decide (section 7) |
| Stale references scan and rewrite | `services/compat_refs.py`, `services/migrations/refs.py` | finds deprecated names in project files, rewrites ddflow-managed regions, proposes edits elsewhere | reads only the alias table; a project's own deprecation is invisible to it |
| Upgrade manifest | `templates/upgrade/changes.toml`, `unreleased/*.toml` fragments, `services/upgrade_manifest.py` | per-release `knob_added/changed/removed`, `event_kind_added/removed`, `refresh`, `repair`, `feature`; `impact` additive/deprecating/breaking; release lint (`ddflow version lint`) | no `deprecated` change kind (a deprecation is not a removal); no warn-until field; manifest describes what happened, not a commitment |
| Upgrade plan, apply, notice | `upgrade_plan.py`, `upgrade_apply.py`, `upgrade_notice.py` | `ddflow upgrade --plan/--apply`, once per version per machine, one line, shared across brief/hook/MCP handshake via `.ddflow/local/upgrade-notice.json` | one line for the whole upgrade; cannot carry per-deprecation urgency |
| Version impact and cut | `scripts/release_impact.py`, `ddflow version show/lint/cut`, `docs/ddflow/compatibility.md` | bump level from declared impact; lint refuses an undeclared surface change | does not ask "is any deprecation due in this release?" |
| Changelog and release notes | `services/export/kind_changelog.py`, `CHANGELOG_CATEGORIES` includes Deprecated and Removed, Conventional Commit `deprecate` type | Keep-a-Changelog sections generated from done items | entries are hand-categorised per item; not derived from a deprecation record |
| Docs pipeline | `ddflow export` doc kinds, `docs check`, document types as data (P-docs-generic), regions | generated docs with `ddflow:generated` header | no banner or "Deprecated" page produced from a record |
| Interface digest, pins, compat manifests | P-soft-upgrade `SU-digest`, `SU-announce`, `SU-restart-notice`; CN pins | classify soft/hard change, re-announce changed interface | digest says "changed", not "going away on date X" |
| Version skew guard | `D-upgrade-skew-guard`, `compatibility.md` | refuses an older writer on direct conflict | orthogonal: protects data, not API users |
| Doctor, hooks, brief | `doctor`, SessionStart, `ddflow_brief` | stale-reference findings, upgrade line | no "N deprecations active" section |
| Guidance statuses | `services/guidance/record.py` `STATUSES = proposed, accepted, deprecated, superseded, retired, rejected` | rules/decisions can be deprecated | a status label, no window, no notice, no consequences |
| Spec/requirement lifecycle | research `project-specifications.md` (`superseded`, `withdrawn`), `requirements-management.md` (RQM tasks), `user-stories.md` | no `obsolete` state yet in the planned models | needs one shared obsolete state tied to a deprecation |
| Change request with deprecation kind | `CN-cr` (kind `deprecation`, deadline = window), `AI-lifecycle` (decided: NOT a second notification path) | sister-project consumers get a per-consumer burndown | the right channel for CONSUMERS of a shared spec/library; same deadline must come from the same record |
| Promote / deployment tracking | `ddflow promote add/deployed/status` | which sha is live in which environment | gives "is the removal deployed" evidence |
| Event kinds | `core/events.py`, upcasters, `event_kind_removed` in manifest, `FORMAT_LEVEL` | old kinds fold forever; a reader skips unknown ones | rule exists in prose; no tombstone concept for kinds |
| Cadence passes, lessons, decisions | `ddflow cadence`, `lesson add`, `decision add` | periodic reminders; durable rationale | a cadence pass "deprecations due" does not exist |
| Metrics | `ddflow metrics` (summary, skips, lanes) | local spend and gate stats | no per-interface call counters |

### 2.1 Duplicated by a new feature, versus extended

- DUPLICATED if built naively as a new subsystem: the notice channel (use `upgrade_notice` and `Notices`), the alias table (`Alias` is already a deprecation of kind command/flag/tool/argument; a `deprecation` record must reference or generate it, not parallel it), the changelog Deprecated/Removed sections, the CN-cr deprecation request (consumers), the guidance `deprecated` status, the manifest `impact` field.
- EXTENDED: `Alias.removed_in` becomes enforced; the manifest gains one change kind `deprecated` (and `removed` referencing the record); `upgrade --plan` gets a "deprecations" category; `version lint` and `version cut` gain a window check; `kind_changelog` reads records for Deprecated/Removed; export doc kinds gain a generated "deprecations" document; brief/doctor/cadence gain one line each; the data-kind engine (`templates/kinds/*.toml`, lifecycle transitions, links, validators) provides storage, CRUD, history, search and tombstones for free (`risk.toml` is the pattern).
- NEW: the record itself, the window gate, usage counters (opt-in), project-level notice generation.

## 3. Best practices (external)

| Source | Rule worth borrowing | Tag |
|---|---|---|
| Python PEP 387 | warn in at least two minor versions of one major (about two years), preferred five; `DeprecationWarning` normal, `PendingDeprecationWarning` for long coexistence; "soft deprecation" is docs-only and schedules no removal; warning text names the release of change and links an issue; steering council may shorten only for security or broken features; check feedback before removing | FETCHED |
| Python PEP 702 `warnings.deprecated` | decorator with message, category, stacklevel; static checkers flag uses; `category=None` marks without runtime warning | FETCHED |
| Kubernetes policy | window set by stability level: GA not removed within a major; beta deprecated no earlier than 9 months or 3 releases and served 9 months or 3 releases after; alpha removable freely; deprecated API calls return a `Warning` header, an audit annotation and a metric labelled with `removed_release`; stored data must still decode | FETCHED |
| Go 1 compat and GODEBUG | source-compat promise with listed exceptions; behaviour changes get a compat setting kept at least 2 years (4 releases), documented per release with removal plan, each with a counter `/godebug/non-default-behavior/<name>:events`; the `go` line in go.mod pins the defaults | FETCHED |
| Node.js | four kinds: documentation-only, application, runtime, end-of-life; stable `DEPxxxx` codes that survive revocation; flags `--pending-deprecation`, `--throw-deprecation` | FETCHED (semver-major rule: UNVERIFIED, text truncated) |
| GitHub REST API | date-named versions; each old version supported 24 months after the next; near end, `Deprecation` and `Sunset` headers; after retirement `410 Gone`; additive changes appear in all versions | FETCHED |
| Stripe | rolling date versions, account pinned at first call, each breaking change a "version change module" holding docs, transformation and affected types; changelog generated from the modules | FETCHED |
| RFC 9745 / RFC 8594 | `Deprecation: @<unix>` (Structured Field date, past or future), `Sunset: <HTTP date>` never earlier than the deprecation date, `Link: rel="deprecation"` to docs; clients treat as hint, verify the linked page | FETCHED |
| OpenAPI 3.1 | `deprecated: true` boolean on operations and parameters ("SHOULD refrain"); no date field | FETCHED (Schema object part truncated) |
| Google AIP-185 | beta features: 180 days recommended; alpha removable; a feature must not be released already deprecated; breaking changes only in a new version | FETCHED |
| SemVer | deprecation requires a minor bump (item 7); removal belongs to a later major; FAQ: at least one minor release carries the deprecation first | FETCHED |
| Keep a Changelog 1.1 | separate Deprecated and Removed sections; announce so users can step to the release listing deprecations, clean up, then move to the release that removes | FETCHED |
| Rust editions | breaking changes only at an opt-in edition declared per crate; crates of different editions interoperate; `cargo fix --edition` migrates | FETCHED |
| Event-sourcing practice (weak schema, upcasting, never edit the log; Greg Young, Axon docs) | old event types stay readable forever, convert on read | UNVERIFIED (memory; ddflow's own `core/upcasters.py` and compatibility.md already do this) |
| Enterprise SaaS and LTS terms (Azure, Salesforce, Ubuntu LTS and similar: 12 months notice, fixed support periods) | contract obligations are a minimum window, per customer tier | UNVERIFIED |
| Telemetry-informed removal (Chrome deprecation usage counters, Firefox, Go counters) | decide removal date from observed use, never from silence alone | Go counters FETCHED; Chrome UNVERIFIED |

### 3.1 Synthesis: what practice agrees on

1. Tiers, not a switch: announce (docs only) > warn (runtime notice) > error (opt-in or default) > remove. Python, Node.js, Kubernetes all have it.
2. The window is measured in releases AND time, and is a floor: the longer of the two (Kubernetes "9 months or 3 releases, whichever is longer").
3. Window length depends on how stable the thing was promised to be (GA/beta/alpha; ddflow: documented interface versus experimental knob).
4. A deprecation names the replacement, the release it lands in, and a link; a warning that does not name them is a defect (PEP 387).
5. Every channel carries the same facts from one source: changelog, docs, runtime warning, headers. Stripe generates the changelog from the change modules for that reason.
6. Evidence of use informs the date (Go counters, Kubernetes gauge), and the ability to see old behaviour still in use is itself a feature.
7. Pins and compatibility versions let a consumer stay on the old behaviour deliberately (Stripe account pin, Go `go` line, Rust edition). In ddflow: `[upgrade]` and config `format`.
8. Stored data outlives code: Kubernetes keeps decoding stored versions after serving stops; ddflow keeps folding old event kinds.
9. Soft deprecation (docs-only) exists and must not schedule removal (PEP 387).
10. Exceptions (security) exist, are rare, and are recorded.

## 4. Gap analysis against ddflow

| # | Gap | Evidence |
|---|---|---|
| G1 | No durable record of a deprecation decision (why, replacement, audience, window); facts live in code tables and manifest prose | `Alias`, `RENAMED`, `changes.toml` |
| G2 | `removed_in` is a floor, never a trigger: nothing removes an alias or fails a release that overstays | `check_rename`, `MTD-sunset` is a manual task |
| G3 | Conflicting window rules: aliases until 1.0 (D-compat-r2) versus two releases (D-mcp-tool-families, Q12 of CM-decide) | decision texts |
| G4 | Notice is once per process/session with no persistence across sessions and no per-deprecation severity; the upgrade notice is one line per version | `Notices`, `upgrade_notice` |
| G5 | No usage evidence: cannot tell whether anyone still calls an old tool | no counters in `metrics` |
| G6 | No docs output: the README tool table, help and docs do not show a deprecation banner generated from facts | docs pipeline |
| G7 | Projects managed by ddflow cannot deprecate their own features with the same machinery | alias table is ddflow's |
| G8 | Requirements and specs have no `obsolete`/`withdrawn` link to a deprecation; stories for a removed feature stay green | P-requirements, P-specs research |
| G9 | Event kinds have no tombstone concept | `event_kind_removed` in manifest only |
| G10 | `version cut` ignores deprecations (does not require a migration note, does not stop an early removal) | `version lint` compares knobs and kinds only |

## 5. Proposed design (summary; the decision file has the questions)

### 5.1 One record kind, `deprecation`

A data kind in `ddflow/templates/kinds/deprecation.toml`, managed by the generic record engine (add, edit, remove as tombstone, revise, history, search, change feed), following `risk.toml` for lifecycle, links and validators. Fields:

- identity: `id` (`DEPR-<slug>`), `title`, `subject_kind` (`knob`, `command`, `flag`, `tool`, `argument`, `event_kind`, `doc`, `feature`, `api`, `requirement`, `spec`), `subject` (the name), `scope` (`ddflow` or `project`).
- timing: `since` (version or date), `warn_from`, `error_from` (optional), `remove_in` (version) and `remove_after` (date), `window` (the effective minimum, computed from tier and policy), `tier` (`stable`, `beta`, `experimental`).
- content: `replacement`, `reason`, `migration` (steps; text or a command that is a registered migration), `docs_url` or `docs_path`, `severity` (`info`, `action`, `breaking`), `audience` (`users`, `agents`, `operators`, `customers`, `integrators`).
- links: `alias` (the `Alias` entry it governs), `requirement`, `spec`, `change_request` (CN-cr id), `migration_id` (upgrade repair), `item` (the removal task), `decision`.
- `channels`: which of cli, mcp, doctor, docs, changelog, webhook apply (defaults by severity).
- `usage`: opt-in counters summary (section 5.4).

Lifecycle (transitions enforced by the engine): `proposed` > `announced` > `warning` > `erroring` (optional) > `removed` > `tombstoned`; `withdrawn` from any state before `removed` (deprecation revoked, Node.js style, id kept). `soft` is not a state: a record with `remove_in` empty is a soft deprecation and the lifecycle never passes `warning` (PEP 387 rule).

### 5.2 Window policy

`[deprecation]` knobs (all layered by D-config-layer-order; company ENFORCED floors, projects may only raise): `min_releases`, `min_days` per tier (defaults proposed: stable 2 releases and 90 days, beta 2 releases and 30 days, experimental 0), `error_tier_enabled`, `usage_required` (see 5.4), `notice_each` (`session`, `day`, `version`). Effective window is the LONGER of releases and days. Tighten-only for company minimums; a waiver is an expiring, scoped record with a reason (as requirements waivers, `RQM-strength`).

### 5.3 Gates (enforced, not requested)

- Removal gate: a task whose declared subject is a record not yet at `removed`-eligible state (`now` past `warn_from + window`, release count met, migration present, usage condition met) cannot complete; message names the missing condition.
- Ship gate: `ddflow version lint`/`cut` fails if a surface name disappears with no record, if a record is `warning` with no replacement or migration text, or a record due for removal is still present after its `remove_in` (the overstay case, G2).
- Test gate: while the old path exists, a regression test for the old path is required (existing rule "a bug is not closed without a regression test"; here: alias tests until the record is `removed`), and after removal a test asserts the tombstone message.
- Docs gate: generated docs must contain every active record (`docs check`).

### 5.4 Usage observation (opt-in)

Counters of calls through deprecated names, kept machine-locally in `.ddflow/local/deprecation-usage.json` (never committed, no payloads, only name, count, first/last seen, client name). Opt-in: `[deprecation].usage = "off"` default; `"local"` records; sharing across a team or company is `"export"` and only through an explicit command, as for other telemetry. Evidence use: `ddflow upgrade --deprecations` shows counts; `usage_required` makes a removal gate demand "no use in N days on machines that opted in" or a recorded waiver. Silence from machines that did not opt in is not evidence, and the report says so. Source also from the event log: calls that write events carry the alias used (new optional payload key `via`), which needs no extra store (additive).

### 5.5 Notification channels (one source, many renderers)

| Channel | Mechanism (reused) | Budget and rule |
|---|---|---|
| CLI/MCP call-time note | `Notices`, now keyed by record id and severity; text from the record | one line, once per session; `error` tier is an error with the replacement |
| Upgrade notice | `upgrade_notice` count line: "3 deprecations active, 1 due this release: `ddflow upgrade --deprecations`" | still one line per version |
| MCP instructions | at most one clause, only while a record with severity `breaking` is in `warning` or later and affects the client's profile | counted inside the `LS-charter` budget (instructions under 1.5K chars); never a list |
| Doctor and brief | one finding per active record due soon; one brief line | brief stays under `session.brief_max_tokens` |
| Docs | export doc kind `deprecations` (table + migration steps), banner region on the affected generated page, README tool table marks aliases | generated, drift-checked by `docs check` |
| Changelog and release notes | `kind_changelog` takes Deprecated and Removed entries from records whose `since` or `remove_in` equals the release | replaces hand-categorising for these two sections |
| Upgrade manifest | new change kind `deprecated` (keys record id) beside `knob_removed`; `impact = "deprecating"` | the release lint requires it |
| Alert/webhook | a hook point event `deprecation.due` consumed by existing notification and ask/approval surfaces (CN-cr for consumers, `external sync`/webhook companions) | ddflow emits the event; delivery is the integration's job; opt-in |
| HTTP-style headers | only where a project exposes an HTTP API: the record renders `Deprecation`/`Sunset`/`Link` header values (RFC 9745/8594) for the project to serve | generated text, not served by ddflow |

### 5.6 Surfaces without a new MCP tool

- Storage and CRUD: the existing data-kind surface, `ddflow record deprecation add|edit|show|list|search|revise|history|remove`. In the tool-diet matrix `ddflow_record` folds into family `ddflow_doctor` (research mcp-tool-diet.md table), so MCP is `ddflow_doctor action=record kind=deprecation`. No new family, no new action; the `kind` enum gains a value.
- Reporting and apply: `ddflow upgrade --deprecations` (view: active, due, overdue, usage) and `ddflow upgrade --apply deprecations` (rewrite managed references, run the record's migration). MCP: `ddflow_admin action=upgrade` (the `upgrade` family action already exists) with the same flag.
- Window check: `ddflow version lint` and `ddflow version cut` (MCP `ddflow_ship action=version_cut`).
- Documents: `ddflow export deprecations` (MCP `ddflow_view` family action export/docs).
- Prompt-based management: the existing prompt templates for record kinds plus a `deprecate` prompt ("deprecate X, remove in two releases, replacement Y") that fills the add command; the agent reads `ddflow help deprecation`. Natural language never writes without showing the record first.
- Dependency: the family names in this section come from `MTD-family-spec` (running) and `MTD-profiles`; `DEP-mcp` waits for them and changes nothing if they rename.

### 5.7 Layering

Company defaults and ENFORCED floors, project, user and machine-local follow `D-config-layer-order` (`r-company-workflows`, `r-everything-configurable`, `r-everything-managed`). Shipped deprecations (ddflow's own) are packaged defaults in the wheel; a project cannot edit them in place (copies only); a company may raise a window or mark a subject "do not remove" and a project may tighten, never shorten. Project records live in `.ddflow/` data; ddflow's shipped ones are never mixed with them (`scope`).

### 5.8 Managed projects (not only ddflow)

A project using ddflow records its OWN deprecations (a REST endpoint, a CLI flag, a library function, a feature flag, a doc page) with `scope = project`. ddflow then supplies: window floor from company policy; migration text and replacement; generated `DEPRECATIONS.md` and per-page banner regions; Deprecated/Removed changelog and release-notes sections; a customer-facing notice document template for email or portal (rendered text, not sent); `Deprecation`/`Sunset` header values; a gate on the project's own removal task; usage counts if the project reports them (a `deprecation usage` command that accepts counts from its own telemetry, a file, or a metric query, since ddflow cannot observe a customer's traffic); a CN-cr `deprecation` request to sister-project consumers (the consumer path stays CN-cr, `AI-lifecycle` note). Ties: a requirement or spec whose subject has a record in `warning` or later gets state `obsolete`/`withdrawn` automatically at `removed` (computed, not typed), stories under it close as `obsolete` not `done`, and `suspect` marking fires on dependents (`RQM-trace`, `SPEC-trace`). `version cut` includes the project's release notes with the Deprecated and Removed sections. `promote deployed` supplies the evidence that the removal is live.

### 5.9 Replay safety and tombstones

Event kinds are never deleted from the code that folds the log; a removed feature's events keep folding (a no-op handler or an upcaster). A `deprecation` record for `subject_kind = event_kind` ends in `tombstoned`: the writer is gone, the reader stays, the manifest says `event_kind_removed`, and `tests/test_event_upcasters.py` keeps one fixture event per tombstoned kind. For knobs, a tombstoned knob is read, ignored, preserved on write (D-compat-r2 2), reported by doctor as "unknown knob, was removed in X" instead of a generic unknown. For commands and tools, a tombstone is a stub that answers with the removal message and the replacement (like GitHub's `410 Gone`), not "unknown command". The tombstone is removable only at a major version (Kubernetes rule 1 analogue), recorded.

## 6. Retrofit of ddflow's own deprecations

1. Inventory every `Alias`, `RENAMED` entry, deprecated MCP argument, `[enforce]` trailer knob (CM Q12), the 123 old flat MCP tool names (MTD-aliases) and event kinds listed as removed in `changes.toml`.
2. Generate one `deprecation` record per family of aliases (not per tool: about 100 hidden tool aliases are ONE record `DEPR-mcp-flat-tools` with a list), each with `since`, `remove_in` from the decision and a replacement mapping.
3. Make `Alias.removed_in` and `since` read from the record (the record is the source; the registry keeps a generated table, and the old direct declaration keeps working through the same alias-of-the-alias discipline).
4. Add the `deprecated` change kind to the manifest and backfill existing fragments.
5. Resolve G3 first (question Q1): no retrofit date is valid until the operator picks between "until 1.0" and "N releases".

## 7. Contradiction to settle before building

D-compat-r2 (operator, amended 2026-10-17): every renamed or removed CLI command, flag, MCP tool, argument and config key keeps working "until ddflow 1.0, never removed before". D-mcp-tool-families (operator 2026-10-19) and D-mcp-tool-diet (2026-10-22): old tool names are aliases for two releases, then `MTD-sunset` removes them. CM-decide Q12: `[enforce]` knobs aliases for two releases. Proposed reconciliation (Q1): 1.0 stays the floor for CLI commands/flags and config keys of documented interfaces (stable tier); MCP tool names and ddflow's own experimental knobs are the beta tier with the two-release window. This keeps the operator's words true in both places.

## 8. Risks and falsifiers

- A record per alias would bloat the log and brief: mitigated by one record per family.
- Usage opt-in may produce no evidence: then the gate falls back to the time-and-releases window and says it had no evidence.
- A window longer than the release cadence could keep dead code for years: the window is a floor, an `expires` review date is required on any "do not remove" mark.
- Falsifier of the design: if the existing manifest plus `Alias` already carried since/remove/replacement/severity, a record would be a duplicate. The inventory shows they carry since/removed_in/replacement only for four alias kinds and nothing for knobs removal timing, severity, audience, migration, usage or project scope, so the record is not a duplicate; it is the source those code tables should read.
- Unverified items above (enterprise terms, Chrome counters, event-sourcing sources) are not relied on by any gate.

## 9. Sources

Fetched 2026-10-22: peps.python.org/pep-0387, peps.python.org/pep-0702, kubernetes.io/docs/reference/using-api/deprecation-policy (first 100000 of 112021 characters), rfc-editor.org/rfc/rfc9745, nodejs.org/api/deprecations.html (first 100000 characters), go.dev/doc/go1compat, go.dev/doc/godebug, docs.github.com/en/rest/about-the-rest-api/api-versions, stripe.com/blog/api-versioning, keepachangelog.com/en/1.1.0, semver.org, doc.rust-lang.org/edition-guide/editions, spec.openapis.org/oas/v3.1.0.html (first 100000 characters), google.aip.dev/185.
