# Default output caps

`board`, `doctor`, `recover`, `next` and `status` print a bounded default, on the command
line and over MCP alike. A cut answer ends in a footer that names the exact next call:

    [lists cut: blocked 212 total (cap [output].max_rows=50). next page `ddflow next --cursor 50`
     (MCP: `ddflow_next cursor=50`); narrow with `ddflow next --phase ID` (MCP: `ddflow_next phase=ID`);
     everything: `ddflow next --full` (MCP: `ddflow_next full=true`)]

- `--full` (MCP `full=true`) lifts every cap for that call.
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
| `allow_full` | true | false (an org lock) makes `--full` still return the capped answer, saying so |

Each is set in `.ddflow/config.toml`, the git-ignored local layer, the environment
(`DDFLOW_OUTPUT_MAX_ROWS`) or `ddflow config set output.max_rows N`; an org layer locks them.

A `--json` body that carries both prose and rows (`board`) gives each half of `max_chars`;
rows only ever halve (down to 1) until the body fits, so the cut is bounded.
