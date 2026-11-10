# SLIs and SLOs for ddflow itself

Decision document for task `OBS-slo-set`, filed by phase `P-perf-metrics`. Targets are
`[slo]` knobs, so each is set four ways like every other knob. The SLI table is computed
by `ddflow metrics slo` (`ddflow/services/metrics/slo.py`), which reads only the log the
metrics service has already folded — no new store, scheduler or network
(D-sched-no-daemon, D-no-duckdb).

## The SLI table

Each row is a service-level indicator derived from existing metrics, held against a
`[slo]` target:

| SLI | source | target knob | burn-rate view |
|-----|--------|-------------|----------------|
| command latency (brief, claim p95) | PM-sampler per-command samples | `slo.target_command_p95_s` | no |
| gate p90 | `metrics.summary.gate_durations` | `slo.target_gate_p90_s` | no |
| queue age | `metrics.summary.queue_waits` | `slo.target_queue_age_s` | **yes** |
| unavailable rate per gate | `metrics.summary.outcome_table` | `slo.target_unavailable_rate` | no |
| stuck lanes | live held leases at report time | `slo.target_stuck_lanes` | no |
| CI false-red rate | red-main merges (`metrics.lock_red_main_window_s`) | `slo.target_ci_false_red_rate` | **yes** |
| log growth per item | committed events per completed item | `slo.target_log_events_per_item` | no |
| review-finding signal | critic/rubber_duck RAN-versus-failed share | `slo.target_review_finding_rate` | no (signal) |

Status per row is `n/a` under `[metrics].min_samples` (never a fabricated zero), `ok`, or
`BREACH` (over target). The two burn-rate rows also report how deep the SLI already is into
its budget (`budget_multiple`) and a time-to-exhaustion; they are flagged `burning` when
the budget is spent within `slo.burn_rate_exhaust_s`.

## Decisions

- **Burn-rate view only for queue age and CI false-red.** Their window is long and their
  signal monotone; every other SLI rides the existing N-of-W flow-control pattern
  (PM-detect), so it reports a single value against its target and no burn rate.
- **Review failure is a signal, not an outage.** Critic and rubber_duck rounds that raise a
  finding (the 55-57% fail rate) mean the review is doing its job, not that the service is
  down, so `review_finding_rate` is reported but never turned into a breach.

## Falsifier

The table must discriminate a good week from a bad week on the real log. `tests/test_slo_set.py`
folds a good week and a bad week and asserts the bad week's gate p90 and queue age breach
while the good week's do not.

Governs: `ddflow/services/metrics/slo.py`, `ddflow/config_sections/slo.py`,
`tests/test_slo_set.py`
