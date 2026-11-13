# Adapter family `config_store`

Port: **ConfigStore**, declared in `ddflow/adapters/config_store.py` and marked `@port`.

One port over ddflow's own layered configuration — packaged defaults, the committed
`ddflow/config.toml`, the machine-local file, env and session overrides — read and written
as a single unit, so a caller never reaches `ddflow.config` / `config_sections/_layers.py` /
`services/configwrite` directly. A new config SOURCE (an org pack, a remote store) is a
second adapter behind the same port (D-adapters-answers-2: a new source is an adapter, a new
FORMAT is not — ddflow's config stays TOML).

- **Default adapter:** `LocalConfigStore` in `ddflow.services.config_store`; `default_store()`
  builds the shared instance. It lives in `services`, not `adapters`, because the bottom
  `adapters` layer must not reach up into `ddflow.config` / `services.configwrite`
  (r-core-pure).
- **Fake:** `FakeConfigStore` in `ddflow/adapters/fakes/config_store.py`: one in-memory layer,
  no disk, no env; keeps the exact string/bool typing of `configwrite`.
- **Conformance cases:** tests/test_adapter_config_store.py (`CONFORMANCE["ConfigStore"]`,
  run against `LocalConfigStore` and `FakeConfigStore`).
- **Home note:** `ddflow/adapters/config_store` (compat: call sites may still import
  `ddflow.config.Config` / `services.configwrite`).

The pattern is in [README.md](README.md).
