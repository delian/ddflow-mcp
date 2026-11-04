# Adapter family `toml`

Port: **TomlIo**, declared in `ddflow/adapters/toml.py` and marked `@port`. (The layered
overlay loader and comment-preserving key edits are the older **Toml** port in
`ddflow/adapters/tomlcfg.py`; it builds on this one.)

Read side: `loads(text)`, `read(path)`. The write side (`dumps`, `document`, over `tomlkit`)
lands with ADP-toml-contract.

- **Default adapter:** `TomlLibIo` in the same module: `tomllib` reads. Parse failures are `TomlError` (`tomllib.TOMLDecodeError`).
- **Fake:** `FakeTomlIo`, a small TOML subset (tables, JSON-shaped values) over an in-memory
  `files` dict.
- **Conformance cases:** tests/test_adapter_toml.py.
- **Contract:** `toml-home` (.importlinter, task ADP-toml-contract): only `ddflow.adapters.toml` imports `tomllib` and
  `tomlkit`; the other importers sit in a shrink-only baseline (both land with ADP-toml-contract). New code calls the port.

The pattern is in [README.md](README.md).
