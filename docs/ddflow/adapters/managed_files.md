# Adapter family `managed_files`

Port: **ManagedFile**, declared in `ddflow/adapters/managed_files.py` and marked `@port`.

Files shared with other tools (`settings.json`, `.mcp.json`, `opencode.json`, `hooks.toml`,
`AGENTS.md`/`README` regions) are merged, never clobbered: this port owns ONLY the keys ddflow
was asked to write and leaves every other byte alone. One format adapter per shape; JSON
(and JSONC) are handled only here (D-adapters-answers-2). Ownership is recorded under a
reserved `_ddflow` marker: `{"owner": "<writer version>", "keys": [<owned key>, ...]}`.

- **Default adapter:** `JsonManagedFile` in `ddflow.adapters.managed_files` (`merge` /
  `remove` / `owned`); a `.ddflow-bak` sits beside the file before the first write.
- **Fake:** `FakeManagedFile` in `ddflow/adapters/fakes/managed_files.py`: merges in memory.
- **Conformance cases:** tests/test_adapter_managed_files.py (`CONFORMANCE["ManagedFile"]`,
  run against `JsonManagedFile` and `FakeManagedFile`).

The pattern is in [README.md](README.md).
