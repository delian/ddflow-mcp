# Adapter family `env`

Port: **Env**, declared in `ddflow/adapters/env.py` and marked `@port`.

Read-only process environment: `get(name, default="")` reads live, `snapshot()` is a copy for a child process.

- **Default adapter:** `SystemEnv` in `ddflow.adapters.env` over `os.environ`; `DEFAULT` is the shared instance.
- **Fake:** `FakeEnv` in `ddflow/adapters/fakes/env.py`: a dict the test gives (`set`, `unset`); the machine's environment is never seen.
- **Conformance cases:** tests/test_adapter_env.py.
- **Ratchet:** direct `os.environ` / `os.getenv` reads in `ddflow/core` and `ddflow/services` may not grow per file (`tests/test_adapters_clock_env.py`).

The pattern is in [README.md](README.md).
