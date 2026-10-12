# Gap priorities: which gaps of the 2026-10 survey are built, in what order

Task `B-gap-decide`. Evidence: research `R-gaps-evidence` (confirmed, measured on this
repository's log: 46% of events are `lease.renewed`, `ddflow status` takes 5.4 s at 35 MB
after 13 active days, about 1 GB a year at that rate) and `R-gaps-survey` (21 pages read).
The operator's request of 2026-10-10 is recorded verbatim in the session log.

## Decision

The operator accepted the defaults of this task on 2026-10-10 as **D-gap-defaults**
(accepted). Nothing below is a new decision; this file is the record that decision names.

| # | Task | Verdict | Notes |
|---|---|---|---|
| 1 | `B-gap-log-scale` | build, first | measure, then coalesce renewals and move gate evidence out of line |
| 2 | `B-gap-lanes` | build | lane ledger and restart briefs; ledger and advisor only, no daemon (D-sched-no-daemon) |
| 3 | `B-gap-plan-lint` | build | lint at task and phase add; plan review as a flag |
| 4 | `B-gap-metrics` | build, after 1 to 3 | one log-derived service behind `metrics`, `forecast`, `retro` |
| 5 | `B-gap-affected-tests` | build | import-graph closure only, no partial gate outcome (D-gap-affected-tests-no-partial) |
| 6 | `B-gap-fold-properties` | build | seeded model-based and property tests, no new dependency |
| 7 | `B-gap-spend` | build, after `B-am-ingest` and `B-quota-meters` | budgets in tokens and turns, not dollars |
| 8 | `B-gap-log-backup` | build | doctor notes for unpushed shards, a verify and restore drill |
| 9 | `B-gap-command-trust` | defer: research only | build when ddflow runs on other people's pull requests or CI |
| 10 | `B-gap-checks-publish` | defer | until a second human or CI uses the repository |

Answers to the four questions of the task: (a) the single-operator model holds for the next
quarter, so 9 and 10 are deferred; (b) token and turn budgets; (c) yes, `B-rm-changes`,
`B-af-headroom` and `B-sub-metrics` consume `B-gap-metrics` (re-pointed, done: each lists it
as a dependency); (d) the do-not-build list is accepted.

**Do not build** (the list D-gap-defaults accepted): a web UI or TUI beyond `lanes --watch`, a
process-spawning supervisor daemon, a merge-queue service, ML test selection, bidirectional
Jira or Linear sync, RBAC, LLM-based decomposition inside ddflow, push notifications, a
template marketplace, sandboxing, a second search stack. The question text of this task also
named a hosted dashboard; it is covered by the first item (no web UI) and is not built either.

## Order in the queue

D-phase-order-gaps-first and D-old-work-note-r1 put the P-gaps core
(`B-gap-decide`, `B-gap-lanes`, `B-gap-plan-lint`, `B-gap-log-scale`, `B-gap-metrics`) ahead of
`P-record-contract` and the docs wave. As of 2026-10-12 those five tasks carry priority 10, and
the other four P-gaps tasks (`B-gap-affected-tests`, `B-gap-log-backup`,
`B-gap-fold-properties`, `B-gap-spend`) wait for `BL-coverage-gaps` and run at priority 31.
`B-gap-log-scale` also needs `B-hy-commit-events` and `B-log-snapshot-knobs`.

## Done when

This file exists and names D-gap-defaults (it does), each of the ten above has a verdict and
an order (the table), and the dependent tasks are re-pointed (listed above).
