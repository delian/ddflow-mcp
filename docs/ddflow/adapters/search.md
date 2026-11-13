# Adapter family `search`

Port: **SearchIndex**, declared in `ddflow/adapters/search.py` and marked `@port`.

The SQLite-backed search index the similarity engine reads (`infra.store`'s `similar_*`
tables): `query` reads the postings of the terms asked, `index` writes them into an empty
index, and `rebuild` wipes and rewrites the whole index. Tokenizing and weighting stay in
`core.textsim`; this port is only the SQLite storage and retrieval seam, so a caller
reaches the index through it instead of importing `sqlite3`.

- **Default adapter:** `SqliteSearchIndex` (`DEFAULT`): the `similar_docs` / `similar_terms`
  tables; `sqlite3` lives here alone.
- **Fake:** `FakeSearchIndex` in `ddflow/adapters/fakes/search.py`: the caller's rows and
  postings held in memory.
- **Conformance cases:** tests/test_adapter_search.py.

The sibling families `fuzzy` (rapidfuzz, the `[search]` extra) and `embed` (model2vec, the
`[rag]` extra) are one port each; a second implementation of either is a bug.
