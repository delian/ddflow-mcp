# Adapter family `forge`

Port: **Forge**, declared in `ddflow/adapters/forge.py` and marked `@port`.

A forge reached through its CLI, holding no token and speaking no HTTP. Multi-adapter: GitHub and GitLab, keyed by `name` in a `Registry[Forge]`.

- **Default adapter:** `ddflow.infra.forge` today.
- **Fake:** none yet (baseline `tests/guard_baselines/ports_without_fake/Forge.txt`)
- **Conformance cases:** none yet (they arrive with the fake).

The pattern is in [README.md](README.md).
