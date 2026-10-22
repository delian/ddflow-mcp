# Deprecation lifecycle: decision proposals

Status: **ACCEPTED 2026-10-22** (operator answered Q1-Q12; every answer equals the recommended default; see the answers table at the end). D-DEP-1..8 are accepted as proposed, with the answers refining them. Decided in task `DEP-decide`. Evidence: `docs/ddflow/research/deprecation-lifecycle.md` (section numbers below refer to it). Phase: `P-deprecation` (prefix `DEP-`). Governing records: `D-compat-r2`, `D-mcp-tool-diet`, `D-mcp-tool-families`, `D-config-layer-order`, `D-universal-principles`, `r-everything-configurable`, `r-everything-managed`, `r-company-workflows`, `D-old-work-behind-core-docs-accepted`. Related phases: P-soft-upgrade, P-tool-diet, P-commit-messages, P-requirements, P-specs, P-lean-start.

## D-DEP-1 One managed record kind, not a new subsystem

A `deprecation` data kind on the generic record engine (`ddflow/templates/kinds/deprecation.toml`, following `risk.toml`) holds subject, since, window, replacement, migration, severity, audience, channels, links and a lifecycle. Existing code tables (`Alias`, `RENAMED`, manifest fragments) become generated from or checked against it. Rejected: extending `Alias` alone (cannot hold knobs, docs, features, project scope, usage); a separate deprecation service with its own store (violates `r-everything-managed`).

## D-DEP-2 Lifecycle and the window is a floor

States `proposed > announced > warning > erroring > removed > tombstoned`, plus `withdrawn`. A record with no `remove_in` is a soft deprecation and never schedules removal. The effective window is the longer of N releases and D days, by tier. Rejected: removal by calendar date alone (a release cadence can stall); removal by release count alone.

## D-DEP-3 Enforcement at the gates ddflow already has

Removal tasks cannot complete early; `version lint/cut` fails on an overstayed or undocumented deprecation; the old path keeps a regression test until `removed`; `docs check` requires generated docs for active records. Rejected: advisory-only warnings (the project's own rule: enforced, not requested).

## D-DEP-4 Notification from one source, within budgets

CLI/MCP call-time note (once per session), upgrade notice count line, doctor and brief lines, docs banner and generated `deprecations` document, changelog Deprecated/Removed, manifest `deprecated` change kind, and a `deprecation.due` hook event for alerts and webhooks. The MCP instructions carry at most one clause and only for `breaking` severity, inside the LS-charter budget. Rejected: a new notification path (`AI-lifecycle` already decided a deprecation is a CN-cr change request for consumers, not a second path).

## D-DEP-5 Usage observation is opt-in and local

Counters of deprecated-name calls in a machine-local file, off by default; absence of data is reported as "no evidence", never as "unused". Sharing is explicit. Rejected: default-on telemetry.

## D-DEP-6 No new MCP tool

CRUD through the existing record surface (`ddflow record deprecation ...`; MCP family `ddflow_doctor action=record kind=deprecation` in the tool-diet matrix), reports through `ddflow upgrade --deprecations` (`ddflow_admin action=upgrade`), window check inside `version lint/cut` (`ddflow_ship`), document through `export` (`ddflow_view`). Prompt management through the record prompt plus a `deprecate` prompt template. Rejected: a `ddflow_deprecation` tool (adds to the 24-tool budget for no capability).

## D-DEP-7 Tombstones, never deletions

Event kinds stay readable forever (replay); a removed knob is read-ignored-preserved and doctor names its removal release; a removed command/tool answers with a removal stub naming the replacement. Stubs are dropped only at a major version, recorded. Rejected: deleting handlers for removed event kinds.

## D-DEP-8 Managed projects use the same kind

`scope = project` records for a project's own features; generated notice documents; requirement/spec subjects follow the record (`obsolete`/`withdrawn` computed); sister-project consumers are told through CN-cr; layering per `D-config-layer-order`, company floors tighten-only.

## Questions for the operator (DEP-decide)

| Q | Question | Recommended default |
|---|---|---|
| Q1 | D-compat-r2 says aliases are never removed before 1.0; D-mcp-tool-families and CM-decide Q12 say two releases. Which governs? | Tiers: documented CLI commands/flags and config keys keep "until 1.0" (stable tier); MCP tool names, `[enforce]` aliases and experimental knobs are the beta tier, two releases AND 30 days. Supersede neither text; add the tier to D-compat-r2. |
| Q2 | Window floors by tier | stable: 2 releases and 90 days; beta: 2 releases and 30 days; experimental: none |
| Q3 | Is a `deprecation` record required for every renamed/removed public name (enforced by `version lint`)? | Yes, one per family of aliases, not per alias |
| Q4 | Usage observation | Opt-in local counters, off by default; removal gate asks for "no recorded use or a waiver", and says "no evidence" when nobody opted in |
| Q5 | Include an `erroring` tier (old path errors unless `--allow-deprecated`) before removal? | Yes, one release before removal, off for beta tier |
| Q6 | MCP instructions mention a deprecation? | Only one short clause while a `breaking` record is in warning or later, inside the 1.5K char charter |
| Q7 | Alerts/webhooks | Emit `deprecation.due` event and a rendered message; delivery by an integration, opt-in; no network call from core |
| Q8 | Who may shorten a window? | Nobody without an expiring recorded waiver (reason required); company floors cannot be shortened by a project; security exceptions need `severity = security` and a lesson |
| Q9 | Project (customer-facing) deprecations in this phase? | Yes, `scope = project`, generated DEPRECATIONS.md, notice template, Deprecation/Sunset header text; nothing sent by ddflow |
| Q10 | Requirement/spec `obsolete` state | Computed from the record reaching `removed`; stories close `obsolete`; needs a shared state name agreed with RQM-model and SPEC-gate (declared dependency) |
| Q11 | Event kinds: tombstone only, deletion never | Agreed default; stubs removed at a major only |
| Q12 | Retrofit ddflow's own deprecations as records, with `Alias.removed_in` read from them | Yes, after Q1 is answered; one record per alias family |

## Answers (operator, 2026-10-22)

All twelve answers accept the recommended default. Each is recorded as decision `D-DEP-Q<n>`; Q1 is also added to `D-compat-r2` as the tier amendment (neither text superseded).

| Q | Answer | Decision | Proposals | Effect on tasks |
|---|---|---|---|---|
| Q1 | Accepted: stable tier (documented CLI commands/flags, config keys) keeps "until 1.0"; beta tier (MCP tool names, `[enforce]` aliases, experimental knobs) two releases AND 30 days; tier added to `D-compat-r2` | D-DEP-Q1, D-compat-r2 | D-DEP-2 amended | DEP-inventory, DEP-retrofit, DEP-record |
| Q2 | Accepted: stable 2 releases + 90 days; beta 2 releases + 30 days; experimental none | D-DEP-Q2 | D-DEP-2 accepted | DEP-lifecycle |
| Q3 | Accepted: record required per renamed/removed public name, one per alias family, enforced by version lint | D-DEP-Q3 | D-DEP-1, D-DEP-3 accepted | DEP-lifecycle, DEP-inventory, DEP-record |
| Q4 | Accepted: opt-in local counters, off by default; removal gate asks for "no recorded use or a waiver" and says "no evidence" when nobody opted in | D-DEP-Q4 | D-DEP-5 accepted | DEP-usage, DEP-lifecycle |
| Q5 | Accepted: erroring stage one release before removal, stable tier only | D-DEP-Q5 | D-DEP-2 amended | DEP-lifecycle, DEP-e2e |
| Q6 | Accepted: one short MCP-instructions clause only while a breaking record is in warning or later, within the charter budget | D-DEP-Q6 | D-DEP-4 accepted | DEP-notify |
| Q7 | Accepted: `deprecation.due` event + rendered message; delivery is an opt-in integration, no network call from core | D-DEP-Q7 | D-DEP-4 accepted | DEP-notify |
| Q8 | Accepted: shortening needs an expiring recorded waiver with a reason; projects cannot shorten company floors; security exceptions need `severity = security` and a lesson | D-DEP-Q8 | D-DEP-2 amended | DEP-record, DEP-lifecycle |
| Q9 | Accepted: `scope = project`, generated DEPRECATIONS.md, notice template, Deprecation/Sunset header text; ddflow sends nothing | D-DEP-Q9 | D-DEP-8 accepted | DEP-project |
| Q10 | Accepted: `obsolete` computed from the record reaching `removed`; stories close `obsolete`; state name shared with RQM-model and SPEC-gate | D-DEP-Q10 | D-DEP-8 accepted | DEP-project |
| Q11 | Accepted: event kinds tombstone only; stubs removed at a major only | D-DEP-Q11 | D-DEP-7 accepted | DEP-retrofit, DEP-e2e |
| Q12 | Accepted: retrofit as records after Q1, one per alias family, `Alias.removed_in` read from them | D-DEP-Q12 | D-DEP-1 accepted | DEP-retrofit |

Decisions D-DEP-1 to D-DEP-8 above are the design these answers ratify; they are accepted, with D-DEP-2 amended by Q1, Q5 and Q8 as shown.
