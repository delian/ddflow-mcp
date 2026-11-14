# Perf metrics

How slow, how expensive and how it is trending — measured, reported, and read without
trial and error. The system has two sides:

- **The perf report** (`ddflow perf report`) reads the committed event log for a window
  and renders per-gate time and pass rate, the cycle-time split, lease and merge wait,
  rework, and the per-model / per-reviewer / per-agent comparison, as chat text, JSON,
  SVG or HTML.
- **The measuring wrapper** (`ddflow perf run` / `ddflow perf ingest`) measures one
  command's wall, cpu, rss and io on this host, and accepts a sample line that another
  agent reports from a host where it could not run itself.

Raw samples stay in `.ddflow/local`; only small per-run rollups reach the committed log.

## What to run for each question

| The operator asks | Run |
| --- | --- |
| how slow, which gate is slowest, where the time goes | `ddflow perf report` |
| a trend — is the suite slower this month | `ddflow perf report` over each window, compared by hand; deltas are named, not yet computed |
| which reviewer, model or agent is worth it | `ddflow perf report --format text` (the comparison rows) |
| one phase only | `ddflow perf report --filters phase=<id>` |
| a chart to open or send | `ddflow perf report --format html --out report.html` |
| every row for a tool | `ddflow perf report --format json` |
| this machine's class fingerprint | `ddflow perf env` |
| one command measured on this host | `ddflow perf run --cmd <CMD>` |
| a line reported by another host | `ddflow perf ingest --line <LINE>` |

The same surfaces in MCP form are `ddflow_perf action=report` and `ddflow_view action=perf`
with `format=`, `window=`, `filters=` and `out=`; `ddflow_perf action=run` and
`ddflow_perf action=ingest` cover the wrapper. Log-derived cycle time, review rounds,
waits, forecast and retro are a different command: `ddflow metrics`.

## How to read the figures

- **Every figure carries an `n`.** Below `[metrics].min_samples` the value is `n/a`
  (not-enough-data), never zero.
- **`p50` / `p90`** are durations over the window. The headline lists the slowest gates;
  the `gates` rows add first-run pass rate and the ever-failed share.
- **First-run pass rate** counts verdicts that RAN the gate; a `skip` and an
  `unavailable` are excluded from both the rate and `n`, so neither a pass nor a zero.
- **Comparisons** of model, reviewer and agent are always present; below the floor they
  say not-enough-data instead of a claim.
- **`not collected`** names a section whose source is not merged yet, with the task that
  ships it. It is named, never rendered as an empty table or a zero.

## Caveats

- One window is a snapshot: `slowest` is absolute, and a before/after comparison needs
  two windows. Period-over-period deltas are computed by a later task and are named
  under not-collected until then.
- A probe that could not run is recorded `unavailable`, never zero or passed.
- Raw samples are local and retention-capped; no prompt text, command arguments, user
  path or secret appears in any metric.
- The report answer names its exact next call in CLI and MCP form so the operator can
  keep driving it without guessing.

## Failed tests first, and the time to the first failure

A `ci run` of an item that failed before runs the tests that failed then FIRST, so a test that
is still broken is reported in seconds instead of at the end of a run that costs minutes (1005
failed ci runs ran to the end, about 275 hours, p50 472 s). The ids come from ddflow's own
evidence, not pytest's cache (ddflow runs in scratch worktrees under xdist): the item's last
failed `ci` and `unit_tests` records, the whole list from the blob the run kept aside, at most
`[suites].failed_first_max_ids`; after them come the tests of the files the diff touches. They
reach the project's pytest as a plugin copy and a file named by `DDFLOW_FAILED_FIRST_FILE`,
applied in `pytest_collection_modifyitems` before xdist distributes. A missing ids file is the
ordinary order, never an error.

The run's evidence (`ci_scope`) carries `ran_failed_first` (the ids ordered first) and
`first_failure_at_s`, the seconds from the first pytest session's start (collection included) to
its first failed test: the figure that shows whether a repeat failure is now seen early and the
data a stop-at-first-new-failure decision needs. `ddflow ci failed-first [--ref ITEM]` (the
`ddflow_ci` action `failed-first`) shows the ids a run would put first. Turn it off with
`ddflow config --set suites.failed_first false`. The `unit_tests` gate does not order yet.

## Knobs

Every threshold and interval is a `[perf_metrics]` knob, settable four ways (config file,
`ddflow config`, MCP, org lock): `report_default_window`, `report_max_rows`,
`report_width`, the sample floors and the drift-detector thresholds. `[metrics].min_samples`
is the floor under which a figure is `n/a`.

## Decisions

Collected by default (decision D-perf-metrics-answers): the cheap set plus the live
sampler; a probe that cannot run is `unavailable`; raw detail is local, aggregates are
committed; network bytes per command are not collected and every report says so.
