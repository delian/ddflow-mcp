# Driver inventory: agent-name sites in code

Checked by `test_the_inventory_matches_the_ratchet_baseline` in `tests/test_agent_literals_substring.py` (file set and per-file counts) against `BASELINE` in
`tests/test_no_agent_literals.py` (same files, same counts). Source: driver-architecture.md
section 2.4 (111 exact sites in 25 files; 68 data, 26 compat, 3 model-axis, 14 driver).
No behaviour changes here: the table names where each site goes, the moves are later items.

Classes: `data` (a table kept as Python literals: moves to a descriptor), `compat` (shim with
a removal date, D-compat-r2, until 1.0), `model-axis` (belongs to the model catalog, not the
harness), `driver` (names a style or format: stays, keyed by a descriptor field).

| File | Sites | Class | Replacing descriptor field |
|---|---|---|---|
| `ddflow/mcpshim/clients.py` | 31 | data | `[client] names` (clientInfo.name strings per harness) |
| `ddflow/services/harnessreg.py` | 6 | data | `[session] env` and `[hooks] style` vocabulary |
| `ddflow/services/adopt/_adopt.py` | 1 | data | `[instructions] file` |
| `ddflow/services/adopt/_docs.py` | 1 | data | `[instructions] file` |
| `ddflow/services/swarm/lanehooks.py` | 3 | data | `[swarm] lanes` doors key |
| `ddflow/surfaces/commands/setup.py` | 11 | compat | `--harness <id>` replaces the --claude/--gemini aliases |
| `ddflow/services/claudehooks.py` | 9 | compat | `[hooks] file` plus the legacy identity kept byte for byte |
| `ddflow/api/setup/hooks.py` | 2 | compat | `--harness <id>` |
| `ddflow/surfaces/declared/hooks.py` | 2 | compat | `harness` argument of ddflow_hooks |
| `ddflow/surfaces/parsers/workflow.py` | 1 | compat | `hooks run --harness` default from descriptors |
| `ddflow/services/legacy.py` | 1 | compat | `[commands] dir` glob |
| `ddflow/adapters/reviewer_clients.py` | 1 | model-axis | route `kind` (models/routes/<id>.toml) |
| `ddflow/services/hookwriters/__init__.py` | 2 | driver | `[hooks] style` |
| `ddflow/services/hookwriters/flatlist.py` | 2 | driver | `[hooks] style` |
| `ddflow/services/mcpconfig.py` | 2 | driver | `[mcp] shape` |
| `ddflow/services/lanes/launch_template.py` | 2 | driver | `[lanes] launch` template set |
| `ddflow/services/lanes/opencode.py` | 2 | driver | `[lanes] binaries` preference order |
| `ddflow/services/leanagents.py` | 1 | driver | `[subagents] template set` |
| `ddflow/services/importer_harness.py` | 1 | driver | `[memory] store` |
| `ddflow/services/harness.py` | 1 | driver | `[instructions] file` (site deleted by B-hx-onboard-generic) |
| `ddflow/core/bookkeeping.py` | 1 | driver | `[worktrees] dir` |
| `ddflow/services/export/kind_security.py` | 1 | driver | none: GitHub's own `.github/SECURITY.md` path |
| `ddflow/surfaces/declared/capflags.py` | 1 | driver | none: the paging `cursor` argument, not the Cursor harness |
| `ddflow/views/output_caps.py` | 2 | driver | none: the paging `cursor` key, not the Cursor harness |

Totals: data 42, compat 26, model-axis 1, driver 18 (87; rows leave as sites move into descriptors).

## Substring surface (report only)

The exact ratchet misses constants that merely contain a name (`"run prompt --harness claude"`).
`test_agent_literals_substring.py` counts them (case-insensitive, ids of 4+ characters;
`MIN_ID_LEN`) and prints the total; a ceiling `SUBSTRING_CEILING` (180: a margin over today's 171) only stops a silent jump.
Today: 171 constants in 59 files (`cline` is not read inside `decline`). Lower the ceiling as sites move.
