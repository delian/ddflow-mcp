"""The index-backed sources of the search core: the tables `index.db` projects from the log.

`Hit` rows are enumerated from the folded state for `ddflow search`. The agent-facing views
(`recall`, `lesson search`, `decision search`) rank the index tables instead, with the store's
fused BM25 + trigram ranking, and the add-time duplicate check scores against the weights
the same index holds. They are all callers of this module (D-unify 3, B-uni-search-core.6):
no API function asks a `Store` to search on its own.
"""

from __future__ import annotations

from typing import Any

from ...config import csv_list
from ...core import participation as PA
from ...infra.store import Store, recall_sources
from ..similar import open_store


def tables() -> tuple[str, ...]:
    """The index tables a recall reads, in the order a reader should weigh them."""
    return tuple(t for t, _, _ in recall_sources())


def search_table(store: Store, table: str, query: str, limit: int) -> list[dict[str, Any]]:
    """The best ``limit`` rows of one index table for ``query``, best first. A declared kind
    is read from the shared ``records`` table, ranked on its own, at most its ``recall_cap``
    rows, and its rows are named by the record's own id."""
    declared = PA.DECLARED.get(table)
    if declared is None:
        return store.search(table, query, limit)
    rows = store.search("records", query, min(limit, declared.recall_cap), prefix=f"{table}:")
    return [{**r, "id": r["rid"]} for r in rows]


def search_sources(
    store: Store, query: str, sources: str, limit: int
) -> dict[str, list[dict[str, Any]]]:
    """The hits of every wanted source, in rank order, by table; a source with none is absent.

    ``sources`` is a comma-separated list of table names or labels (empty: every source). One
    unreadable source does not take the others down: the value is in the union, and "the
    lessons table is corrupt" is not a reason to withhold the decisions.
    """
    want = csv_list(sources) or list(tables())
    lowered = [w.lower() for w in want]
    results: dict[str, list[dict[str, Any]]] = {}
    for table, label, _why in recall_sources():
        if table not in lowered and label.lower() not in lowered:
            continue
        try:
            hits = search_table(store, table, query, limit)
        except Exception:
            hits = []
        if hits:
            results[table] = hits
    return results


def matcher(store: Store):
    """The duplicate-check matcher over the same index (the caller made it current first).

    Raises ``LookupError`` for a missing or outdated index: it never answers from stale
    weights."""
    return open_store(store)
