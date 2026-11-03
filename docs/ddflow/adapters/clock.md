# Adapter family `clock`

Port: **Clock**, declared in `ddflow/adapters/clock.py` and marked `@port`.

The one source of "now": `wall()` (epoch seconds, can jump; stamps and leases), `monotonic()` (steady; durations and the planned perf metrics), `now_utc()` and `sleep()`. Timestamp parsing and the log's ISO form stay in `ddflow.core.clock`.

- **Default adapter:** `SystemClock` in `ddflow.adapters.clock`; `DEFAULT` is the shared instance.
- **Fake:** `FakeClock` in `ddflow/adapters/fakes/clock.py`: frozen, moves on `advance()` and on `sleep()` (nothing waits); `jump_wall()` moves wall time only.
- **Conformance cases:** tests/test_adapter_clock.py.
- **Ratchet:** direct `time.time()` / `time.monotonic()` reads in `ddflow/core` and `ddflow/services` may not grow per file (`tests/test_adapters_clock_env.py`); migrate a site by taking a `Clock`.

The pattern is in [README.md](README.md).
