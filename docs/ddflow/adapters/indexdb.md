# Adapter family `indexdb`

Port: **IndexStore**, declared in `ddflow/adapters/indexdb.py` and marked `@port`.

The sqlite3 seam of the derived index (`ddflow.infra.store`) — the connection, the pragmas,
the schema migration and the index file name — so importing `sqlite3` stays in this one
module (`sqlite-home` import contract). The derived index is a cache over the event log,
gitignored and rebuildable, never a source of truth.

- **Default adapter:** `SqliteIndexStore` in `ddflow.adapters.indexdb`; `DEFAULT` is the
  shared instance.
- **Fake:** `FakeIndexStore` in `ddflow/adapters/fakes/indexdb.py`: opens `:memory:`.
- **Conformance cases:** tests/test_adapter_indexdb.py (`CONFORMANCE["IndexStore"]`).

The pattern is in [README.md](README.md).
