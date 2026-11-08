# Adapter family `tomlcfg`

Port: **Toml**, declared in `ddflow/adapters/tomlcfg.py` and marked `@port`.

The one TOML overlay loader and the one write-back that keeps comments and layout.

- **Default adapter:** `ddflow.infra.tomlcfg` today.
- **Fake:** none yet (baseline `tests/guard_baselines/ports_without_fake/Toml.txt`)
- **Conformance cases:** none yet (they arrive with the fake).

The pattern is in [README.md](README.md).
