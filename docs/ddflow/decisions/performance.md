# Performance: index, rewrite thresholds, budgets, resident state

Decision document for task `B-perf-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-perf: Performance: SQLite FTS5 only, no rewrite now, budgets and resident state as proposed

status **accepted** · decided by operator · recorded by hy-commit-events

**Decision.** Operator 2026-10-13 (AskUserQuestion, B-perf-decide), all defaults accepted. (1) NO DuckDB: D-no-duckdb stands, SQLite FTS5 is the only index (FTS5 measured 10-60x faster per query, 19 ms vs 222 ms startup, 13.5 MB vs 66 MB RSS); revisit an optional ddflow[analytics] extra only if the active log passes 500k events AFTER B-gap-log-scale. (2) NO REWRITE now; thresholds stored as [perf] budgets: hook p50 over 300 ms idle after the Python fixes -> a Rust/Go fast path for the hook subset only; fold+read over 3 s idle at the expected size after checkpoints, or resident MCP RSS over 1 GB, or an active log over 1M events -> a PyO3 core. (3) BUDGETS (idle machine, this repository's log; all configurable): ddflow --version 150 ms; hooks prompt 300 ms; hooks session-start 1.5 s; heartbeat 300 ms; status/next/brief 2 s; MCP initialize 1 s; fresh-process append 100 ms. (4) RESIDENT STATE: the MCP server caches the folded State by log digest; no new daemon (D-sched-no-daemon stands). (5) ORDER: the cheap fixes ride in B-gap-log-scale; the rest of P-perf follows it.

Governs: `ddflow/infra/store.py`, `ddflow/config_sections/perf.py`, `docs/ddflow/decisions/performance.md`

Evidence: R-perf-duckdb-measured, R-perf-profile-2026-10-11

## D-no-duckdb: The local index is SQLite (FTS5/BM25); no DuckDB

status **accepted** · decided by operator · recorded by Monster3-bridge-cse_01FwEow7VXwdwXf48h41tyrB

**Context.** A description of the project claimed a SQLite and DuckDB index; a check found no DuckDB anywhere. The operator chose to correct the claim rather than build to it (2026-09-29).

**Decision.** Search and recall run on the derived SQLite index (.ddflow/index.db, FTS5 with BM25, LIKE fallback). ddflow does not add DuckDB. Semantic recall, if adopted (B-semantic-recall), stores its vectors in the same SQLite index.

**Consequences.** Analytics queries stay on fold/SQLite. At 100k events the cost is the per-call read+fold (R-perf-100k, B166/B169), not the query engine.

**Alternatives rejected.** Add a DuckDB index for analytics (effort, gate rates, trends): rejected — a second runtime dependency and a second derived index to keep consistent with the log, for queries the fold and SQLite already answer.

Governs: `ddflow/infra/store.py`, `ddflow/config.py`
