# Default output caps

`board`, `doctor`, `recover`, `next` and `status` print a bounded default, on the command
line and over MCP alike. A cut answer ends in a footer that names the exact next call:

    [lists cut: blocked 212 total (cap [output].max_rows=50). next page `ddflow next --cursor 50`
     (MCP: `ddflow_next cursor=50`); narrow with `ddflow next --phase ID` (MCP: `ddflow_next phase=ID`);
     everything: `ddflow next --full` (MCP: `ddflow_next full=true`)]

- `--full` (MCP `full=true`) lifts every cap for that call, unless `[output].allow_full = false`: then the capped answer comes back and the footer says the lift is off.
- `--cursor N` (MCP `cursor=N`) resumes a cut list on `next` and `recover`.
- `--json` keeps its schema. Lists are shorter and the body gains `truncated`
  (`lists`: each cut list's real length, `shown`, `chars_cut`) and `next` (the calls as
  `cursor_cli`/`cursor_mcp`, `narrow_cli`/`narrow_mcp`, `full_cli`/`full_mcp`). `recover`'s body
  is a bare array and stays one: the cut is told in the footer (a second MCP content block).

## Knobs (`[output]`)

| knob | default | meaning |
| --- | --- | --- |
| `max_rows` | 50 | rows a list shows; 0 lifts the row cap |
| `max_chars` | 30000 | characters of text or serialised body (about 7500 tokens); 0 lifts it |
| `non_ascii_weight` | 4 | characters of `max_chars` one non-ASCII character of prose costs; 1 counts plain characters |
| `status_fix_tasks_in_headline` | false | whether fix tasks count in the `status` headline pair and the `Progress:` block |
| `status_completed_shown` | 12 | how many recent completions the prose `status` lists (`Completed (last N of TOTAL)`); 0 lists all, unless `allow_full` is false (then 12) |
| `allow_full` | true | false makes `--full` still return the capped answer, saying so, and stops 0 lifting a cap |

Each is set in `.ddflow/config.toml`, the git-ignored local layer, with
`ddflow config output.max_rows N` (MCP `ddflow_configure`), or in the environment
(`DDFLOW_OUTPUT_MAX_ROWS`). `allow_full = false` is a setting, not an enforced lock: ddflow
has no organisation layer yet, so the local layer or `DDFLOW_OUTPUT_ALLOW_FULL` overrides it.

A `--json` body that carries both prose and rows (`board`) gives each half of `max_chars`;
a row's heavy nested fields go first (each becomes a marker of the same type, counted in
`truncated.fields_cut`), then rows only ever halve (down to 1) until the body fits, so the cut is
bounded.

## Status totals (B6341fe42ca)

The `status` headline used to take `len()` of a prose list the caps had already cut to the
row limit ("12/5065 tasks complete" beside `--json` saying 2222 done). The counts now come
from `core.progress.totals.queue_totals`, which owns the task, phase and bug totals; the
prose, `--json status`, the MCP tool, the completion `Progress:` block and the STATUS.md
export all read it. The screen states the definition, `N of M live tasks done (K abandoned
and F fix tasks listed separately)`, and every bucket of every task record
(`done + running + ready + held by cap + blocked + review + abandoned = total`), then phases
and bugs the same way. `--json status` keeps every existing field and adds `tasks.live`,
`tasks.live_done`, `tasks.fix`, `tasks.fix_done`, `tasks.fix_in_headline`,
`tasks.definition` and `bugs`. The `Progress:` block is the live pair of the same function,
labelled "live tasks". The Completed list reads `Completed (last N of TOTAL)` when bounded.
