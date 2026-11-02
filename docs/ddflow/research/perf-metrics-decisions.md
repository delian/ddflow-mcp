# Perf metrics: decisions

Source: decision D-perf-metrics-answers (operator, 2026-11-02), asked after
R-perf-landscape and R-perf-inventory. Phase P-perf-metrics. This document
records the answers; it decides nothing beyond them. The coordinator added the
safeguards below for decision 3, and the defaults in the open-details table are
proposals, not operator answers.

## The eight decisions

1. **Collection default: everything on.** The cheap set (wall, cpu, rss peak,
   io blocks per command, stage times, tokens, tool-call counts) plus the live
   sampler and cgroup scopes where the host offers them. A probe that cannot
   run is recorded `unavailable`, never zero.
2. **Committed data: per-run rollups plus daily aggregates** (p50, p90, max per
   series and environment key). Raw samples stay in `.ddflow/local` under a
   retention cap. No prompt text, command arguments, user paths or secrets in
   any metric.
3. **Autonomy: auto-apply safe proposals, bounded to three kinds:** parallel
   cap and test workers; review cadence and chunk size; quarantine of a proven
   flaky test. Never edits to code, tasks, gates, reviewers or models: those
   stay advice, filed in the review inbox. (The recommended default, advice
   only, was not chosen.)
4. **Comparisons always shown.** Reviewer, model and agent comparisons appear
   in every report with their sample sizes. Below the minimum sample the report
   says "not enough data" instead of a claim.
5. **Report outputs:** text in chat plus SVG and HTML files, returned inline up
   to a bound. Artifact publishing only with consent.
6. **Agent telemetry imported automatically when found** (Claude Code
   OpenTelemetry export, session logs): counts, durations and cost only, never
   prompt text. Shown in `doctor` so the import is visible.
7. **Remote and shell-less servers:** agent-reported timings are stored with
   source `agent-reported`, shown separately, excluded from baselines and
   budgets, and never gate evidence of a pass.
8. **Network bytes per command are not collected** (no portable stdlib way).
   Every report says so.

## Safeguards for auto-apply (decision 3)

- Every automatic change is an event with before and after values.
- It takes effect only inside the knob's floor and ceiling.
- It is measured over a before window and an after window, and reverted
  automatically on regression.
- One call undoes it.
- One kill-switch knob turns auto-apply off; org policy can lock that knob.
- It never overrides a value the operator pinned: `schedule.parallel` set to
  fixed stays fixed unless the operator allows tuning.

## Open details (knobs, not decided by the operator)

Each is a knob in `[perf_metrics]`, with org lock, settable the four ways
(r-everything-configurable). The values are proposed defaults.

| Knob | Proposed default | Note |
| --- | --- | --- |
| retention_days | 14 | Raw samples in `.ddflow/local` |
| min_sample | 8 | Minimum runs before a regression claim |
| min_sample_compare | to set with min_sample | Minimum per agent or model claim (decision 4) |
| rel_threshold | +20 percent | Relative change that counts as regression |
| abs_floor_s | 5 s | Smaller absolute changes are ignored |
| persistence_windows | 2 | Windows a regression must persist |
| sampler_interval_s | 1 s | Live sampler |
| long_gate_s | to propose in PM-budgets | Threshold for a long gate |
| review_cadence_days | 7 | Periodic review pass |

## Tasks of P-perf-metrics and wave order

Read from `ddflow waves` on 2026-11-02. Waves are derived from dependencies and
may shift; rerun `ddflow waves` (MCP: `ddflow_view` action `queue`) for the
current order. Some waves are far apart because the tasks wait on other phases.

- Wave 0: PM-perf-decide, PM-env-fingerprint, PM-resource-capture,
  PM-cycle-split
- Wave 1: PM-store
- Wave 2: PM-retention, PM-sampler, PM-detect, PM-report-data,
  PM-test-durations, PM-agent-calls, PM-complexity
- Wave 3: PM-budgets, PM-flaky, PM-observe-bridge, PM-prop-flow, PM-remote,
  PM-report-text, PM-report-charts
- Wave 4: PM-prop-tests, PM-report-surfaces
- Wave 5: PM-auto-apply, PM-report-prompt
- Wave 15: PM-quality-signals
- Wave 19: PM-llm-wait
- Wave 20: PM-prop-llm, PM-selfcost
- Wave 21: PM-review-job
- Wave 22: PM-knobs, PM-perf-docs
- Wave 23: PM-perf-acceptance

Conformance for all of them: extend the existing metrics code, no second store
or scheduler (r-reuse-core, D-no-duckdb, D-sched-no-daemon); stdlib first,
optional third-party only behind a port with a fake; files stay under
`[quality].max_file_bytes` (r-small-files).
