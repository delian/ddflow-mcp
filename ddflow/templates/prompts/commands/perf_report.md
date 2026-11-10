Answer the performance question from the perf report, and end with the exact next call.

The report is one surface under one command: `ddflow perf report` (MCP `ddflow_perf action=report`,
also reachable as `ddflow_view action=perf`). Run it; do not answer from memory or by
reading the log by hand. A figure the log cannot support is missing, never guessed.

## 1. Match the question to the call

The report is read over a `--window` (a duration like `7d`/`28d`, or a date `YYYY-MM-DD`)
and scoped by `--filters phase=<id>`. The answer comes back in one renderer, chosen by
`--format`:

- `text` (default) and `md` — the markdown answer, width- and row-bounded, returned in chat.
- `json` — the same report as plain data; it holds every row the text renderer cut.
- `svg` and `html` — the chart set, written to a file (add `--out <path>`) under
  `.ddflow/local/perf/reports/` by default, with the path named in the answer.

Map the words the operator actually uses:

| The operator asks | Run |
| --- | --- |
| how slow, which gate is slowest, where the time goes | `ddflow perf report --format text` |
| a trend — is the suite slower this month | `ddflow perf report` over each window, compared by hand; the delta section is named, not computed |
| which reviewer, model or agent passes first time | `ddflow perf report --format text` (the comparison rows) |
| one phase in isolation | `ddflow perf report --filters phase=<id>` |
| every number, for a tool to use | `ddflow perf report --format json` |
| a chart to open or send | `ddflow perf report --format html --out report.html` |

The report's own footer already names the exact next call in CLI and MCP form (open the
html, narrow the window, export the json). Quote that; do not invent a different command.

## 2. Read the figures honestly

- Every number carries its `n`. Below `[metrics].min_samples` a figure is `n/a`
  (`not-enough-data`), never a zero.
- `p50` / `p90` are durations over the window; the headline lists up to three slowest
  gates, and the `gates` rows show time, first-run pass rate and the ever-failed share.
- `pass` is the first-run pass rate over verdicts that RAN the gate; a `skip` and an
  `unavailable` are excluded from both the rate and `n` — neither a pass nor a zero.
- `cycle`, `waits` and `rework` are one number each (cycle time, lease/merge wait, items
  hit by a later bug); read them as the window's shape, not as cause.
- `not collected` names a section whose SOURCE module is not merged yet, with the task
  that ships it. Say it is not collected; do not render it as an empty table or a zero.

## 3. Caveats, said out loud

- One window is one snapshot: `slowest` is absolute. There is no before/after inside one
  window, so a "slower" claim needs two windows, and period-over-period deltas are not
  collected until their source task lands (Named there, not guessed here).
- A probe that could not run is `unavailable`, never zero or passed.
- Raw samples live under `.ddflow/local`; only small per-run rollups reach the committed
  log, and no prompt text, command arguments, user path or secret is in any metric.
- A small `n` is a caveat. Answer "not enough data", not "is fine".

## 4. Close out

Quote the footer's next call for the question asked — it already carries the format, the
window and (for `svg`/`html`) the out path, with the MCP form beside the CLI one:

    ddflow perf report --format text --window 28d
    ddflow_view action=perf format=text window=28d

Do not substitute a fixed call: the window is the one asked for, and a `--filters
phase=<id>` stays in the call that scoped it.
