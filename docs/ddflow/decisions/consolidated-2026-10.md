# Consolidated decisions (2026-10-15)

Authority, by group. D-roadmap-review item 8 (pure merges may be made as superseding records without asking per group) names export, enum fallback, skills, perf with no-duckdb and the MCP surface. The operator's instruction of 2026-10-15 to this item adds quotas with governor, and the seven ordering notes as ONE current decision (item 4). The upgrade record is not a pure merge: it is the one decision the operator asked for to state D-roadmap-review item 2, and it is the meaning-changing synthesis item 8 would send back as a question, written on that explicit instruction. Item 4 also has the three proposed decisions accepted as they stand. The event log is the source of truth: `ddflow decision show <id>`. Each new record quotes its sources' text unchanged, section by section, under the source id in brackets; each old record is superseded (never deleted), so `ddflow decision show <old>` and a replay still give its text.

| New record | Replaces | Decided by |
| --- | --- | --- |
| D-export-documents | D-export, D-export-selection, D-export-templates, D-export-agent-enable | operator |
| D-enum-fallback | D9b8061fd38, D-enum-fallback-strict, D6fc09b2590, D-fallback-no-remote | agent (two sources carry no operator attribution) |
| D-skills | D-skills-spec-native, D-skills-canonical-store, D-skills-operator-answers | agent (two sources carry no operator attribution) |
| D-perf-index | D-no-duckdb, D-perf | operator |
| D-mcp-surface | D-lean-and-trusted, D-mcp-output-schemas-off, D-tools-list-budget-100k, D-viewer-lists-consolidated | operator |
| D-quotas-governor | D-quotas, D-governor | operator |
| D-roadmap-order | D-pause-after-upgrade, D-order-cleanup-then-upgrade-features, D-phase-order-gaps-first, D-old-work-note-r1, D-perf-core-wave, D-phase-order-core-records-first, D-schedule-early | operator |
| D-upgrade-flow | D-upgrade-auto-check, D-self-upgrade, D-self-upgrade-defaults, D-upgrade-on-mcp-connect | agent (synthesis of D-roadmap-review item 2) |
| D-old-work-behind-core-docs-accepted | D-old-work-behind-core-docs (proposed) | operator |
| D-B191-item-ids-accepted | D-B191-item-ids (proposed) | operator |
| D-gate-ratio-noise-accepted | D-gate-ratio-noise (proposed) | operator |

## Left for the operator

- D-skills: D-skills-canonical-store says sync makes symlinks by default; D-skills-operator-answers says sync defaults to copy. Both are quoted; which governs is the operator's call.
- D-upgrade-flow: D-self-upgrade-defaults has container or MCP start apply safe categories under `[upgrade].on_start = safe`; D-roadmap-review item 2 says only when `[upgrade].auto` is safe. The record states item 2 as the current rule.
- D-roadmap-order: entries are chronological; the pause at B-checkpoint-after-unify and other already-passed steps are quoted as recorded, not edited out.
- Not replaced: D-upgrade-event-kinds, D-upgrade-backups, D-upgrade-config-changes, D-upgrade-manifest-lint, D-upgrade-skew-guard, D-dry-run-from-running-version, D-start-timeout-note (they do not disagree on writes).
