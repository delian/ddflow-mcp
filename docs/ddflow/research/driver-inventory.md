# Driver inventory: agent-name sites in code

Checked by `tests/test_agent_literals_substring.py` against `BASELINE` in
`tests/test_no_agent_literals.py` (same files, same counts). Source: driver-architecture.md
section 2.4 (110 exact sites in 21 files; 65 data, 26 compat, 9 model-axis, 10 driver).
No behaviour changes here: the table names where each site goes, the moves are later items.

Classes: `data` (a table kept as Python literals: moves to a descriptor), `compat` (shim with
a removal date, D-compat-r2, until 1.0), `model-axis` (belongs to the model catalog, not the
harness), `driver` (names a style or format: stays, keyed by a descriptor field).

| File | Sites | Class | Replacing descriptor field |
|---|---|---|---|
| `ddflow/mcpshim/clients.py` | 30 | data | `[client] names` (clientInfo.name strings per harness) |
| `ddflow/services/hookio.py` | 20 | data | `[hooks] normalizer` / `emitter` (dialect registry key) |
| `ddflow/services/harnessreg.py` | 6 | data | `[session] env` and `[hooks] style` vocabulary |
| `ddflow/services/compat_refs.py` | 5 | data | `[commands] dir`, `[skills] dir`, `[instructions] file` |
| `ddflow/config_sections/approvals.py` | 2 | data | `[transcripts] root` |
| `ddflow/services/adopt.py` | 2 | data | `[instructions] file` |
| `ddflow/surfaces/commands/setup.py` | 11 | compat | `--harness <id>` replaces the --claude/--gemini aliases |
| `ddflow/services/claudehooks.py` | 9 | compat | `[hooks] file` plus the legacy identity kept byte for byte |
| `ddflow/api/setup.py` | 2 | compat | `--harness <id>` |
| `ddflow/surfaces/declared/hooks.py` | 2 | compat | `harness` argument of ddflow_hooks |
| `ddflow/surfaces/parsers/workflow.py` | 1 | compat | `hooks run --harness` default from descriptors |
| `ddflow/services/legacy.py` | 1 | compat | `[commands] dir` glob |
| `ddflow/config_sections/agent.py` | 6 | model-axis | model catalog `vendor` (models/<family>.toml) |
| `ddflow/services/review.py` | 3 | model-axis | route `kind` (models/routes/<id>.toml) |
| `ddflow/services/hookwriters/__init__.py` | 2 | driver | `[hooks] style` |
| `ddflow/services/hookwriters/flatlist.py` | 2 | driver | `[hooks] style` |
| `ddflow/services/mcpconfig.py` | 2 | driver | `[mcp] shape` |
| `ddflow/services/leanagents.py` | 1 | driver | `[subagents] template set` |
| `ddflow/services/importer_harness.py` | 1 | driver | `[memory] store` |
| `ddflow/services/harness.py` | 1 | driver | removed by B-hx-onboard-generic |
| `ddflow/core/bookkeeping.py` | 1 | driver | `[worktrees] dir` |

Totals: data 65, compat 26, model-axis 9, driver 10 (110).

## Substring surface (report only)

The exact ratchet misses constants that merely contain a name (`"run prompt --harness claude"`).
`test_agent_literals_substring.py` counts them (case-insensitive, ids of 4+ characters;
`MIN_ID_LEN`) and prints the total; a ceiling `SUBSTRING_CEILING` only stops a silent jump.
Today: 149 constants in 42 files (clients.py 22, review.py 14, parsers/workflow.py 14,
adopt.py 10). The research's 163 also counted model names. Lower the ceiling as sites move.
