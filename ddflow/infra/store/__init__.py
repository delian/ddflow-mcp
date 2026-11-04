"""The SQLite projection — a cache and a search index, never a source of truth.

This database is **gitignored, disposable and rebuildable**. Delete it and
``ddflow rebuild`` re-derives every row from the event log. That is not a slogan; it
is enforced by ``tests/test_store.py::test_dropping_the_db_changes_nothing``, which
compares every query before and after ``rm``.

Keeping the index non-authoritative buys three things a database-of-record cannot:

* two agents on two git branches never conflict, because they merge *log files*, not
  database pages;
* a corrupted index is a 200 ms annoyance instead of a lost project;
* the index schema can change without a migration — bump ``SCHEMA`` and rebuild.

What it is genuinely *for* is retrieval. Folding the log answers "what is the state";
it does not answer "which four of my 400 lessons bear on the task I am about to
start". That is a ranking problem, and FTS5's BM25 is a better answer than anything
worth hand-rolling. Where SQLite was built without FTS5 the store degrades to LIKE
scanning, which is slower and worse-ranked but never absent — a search that vanishes
on some machines is worse than one that is merely weaker.

The module is split by concern: ``_fingerprint`` (what makes an index distinct), the
``Store`` class composed from ``_SchemaMixin``/``_ProjectionMixin``/``_SearchMixin``,
and ``_tables`` (the residue model). ``Store`` stays importable as ``store.Store``.
"""

from __future__ import annotations

from ..log import EventLog as EventLog
from ..log import _flock as _flock
from ._fingerprint import _ENV_ERRNOS as _ENV_ERRNOS
from ._fingerprint import _ENV_SQLITE as _ENV_SQLITE
from ._fingerprint import _INDEX_FILE as _INDEX_FILE
from ._fingerprint import _SEARCH_COLS as _SEARCH_COLS
from ._fingerprint import FOLD_REV as FOLD_REV
from ._fingerprint import INDEX_MAX_FILES as INDEX_MAX_FILES
from ._fingerprint import INDEX_MAX_MB as INDEX_MAX_MB
from ._fingerprint import INDEX_TOUCH_S as INDEX_TOUCH_S
from ._fingerprint import META_MARK as META_MARK
from ._fingerprint import META_SHARD_DIGESTS as META_SHARD_DIGESTS
from ._fingerprint import MIN_FINGERPRINT_SOURCES as MIN_FINGERPRINT_SOURCES
from ._fingerprint import MIN_TERM_CHARS as MIN_TERM_CHARS
from ._fingerprint import REBUILD_LOCK_TIMEOUT_S as REBUILD_LOCK_TIMEOUT_S
from ._fingerprint import REBUILD_TEMP_MAX_AGE_S as REBUILD_TEMP_MAX_AGE_S
from ._fingerprint import SCHEMA as SCHEMA
from ._fingerprint import STALE_INDEX_DAYS as STALE_INDEX_DAYS
from ._fingerprint import _capability_tables as _capability_tables
from ._fingerprint import _environmental as _environmental
from ._fingerprint import _has_fts5 as _has_fts5
from ._fingerprint import _has_trigram as _has_trigram
from ._fingerprint import code_fingerprint as code_fingerprint
from ._fingerprint import collect_over_cap as collect_over_cap
from ._projection import _ProjectionMixin as _ProjectionMixin
from ._schema import _SchemaMixin as _SchemaMixin
from ._search import _FTS_TERMS as _FTS_TERMS
from ._search import _POOL as _POOL
from ._search import _POOL_MIN as _POOL_MIN
from ._search import RECALL_SOURCES as RECALL_SOURCES
from ._search import _fallback_words as _fallback_words
from ._search import _fit as _fit
from ._search import _fts_query as _fts_query
from ._search import _row_key as _row_key
from ._search import _SearchMixin as _SearchMixin
from ._search import _terms as _terms
from ._search import _tri_query as _tri_query
from ._search import recall_sources as recall_sources
from ._search import summarise_row as summarise_row
from ._store import Store as Store
from ._tables import _RECONCILE_BATCH as _RECONCILE_BATCH
from ._tables import _SUMMARY_SLOT as _SUMMARY_SLOT
from ._tables import _TABLES as _TABLES
from ._tables import _TRI_EXPR as _TRI_EXPR
from ._tables import _chunks as _chunks
from ._tables import _fill_trigram as _fill_trigram
from ._tables import _insert_similar as _insert_similar
from ._tables import _project as _project
from ._tables import _reconcile_tables as _reconcile_tables
from ._tables import _role_seq as _role_seq
from ._tables import _Row as _Row
from ._tables import _rows_bugs as _rows_bugs
from ._tables import _rows_cadences as _rows_cadences
from ._tables import _rows_decisions as _rows_decisions
from ._tables import _rows_items as _rows_items
from ._tables import _rows_lessons as _rows_lessons
from ._tables import _rows_memories as _rows_memories
from ._tables import _rows_prompts as _rows_prompts
from ._tables import _rows_records as _rows_records
from ._tables import _rows_research as _rows_research
from ._tables import _seq_role as _seq_role
from ._tables import _Table as _Table
from ._tables import _write_rows as _write_rows
from ._tables import similar_records as similar_records

__all__ = [
    "FOLD_REV",
    "INDEX_MAX_FILES",
    "INDEX_MAX_MB",
    "INDEX_TOUCH_S",
    "META_MARK",
    "META_SHARD_DIGESTS",
    "MIN_FINGERPRINT_SOURCES",
    "MIN_TERM_CHARS",
    "REBUILD_LOCK_TIMEOUT_S",
    "REBUILD_TEMP_MAX_AGE_S",
    "RECALL_SOURCES",
    "SCHEMA",
    "STALE_INDEX_DAYS",
    "EventLog",
    "Store",
    "code_fingerprint",
    "recall_sources",
    "similar_records",
    "summarise_row",
]
