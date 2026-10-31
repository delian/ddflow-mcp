"""The search half of `Store`: ranking, FTS queries, recall sources, summaries."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from ...core import participation as PA
from ...core import porter, textsim
from ...core.rank import TRIGRAM_MIN, bm25, fuse, rerank, substring_bm25
from ...core.textcut import clip
from ._fingerprint import _SEARCH_COLS, MIN_TERM_CHARS
from ._tables import _role_seq, _seq_role


class _SearchMixin:
    # -- search ---------------------------------------------------------------------
    def search(
        self,
        table: str,
        query: str,
        limit: int = 5,
        *,
        rerank_by_likeness: bool = False,
        prefix: str = "",
    ) -> list[dict[str, Any]]:
        """Rank ``table`` against ``query``: BM25 over the porter words fused (reciprocal
        rank) with a trigram BM25 over the substrings, best first.

        Both lists exist on every machine. Where SQLite has FTS5 they come from its indexes;
        where it has none (or no trigram tokenizer) the same lists are computed in Python
        over the same columns and the same query terms. Both lists are identical in their
        candidates everywhere: the substring list by construction, the WORD list because the
        fallback splits words as FTS5's unicode61 tokenizer does (`textsim.fts_words`) and
        stems them as its porter tokenizer does (`core.porter`), each checked against FTS5 (for ASCII,
        Latin and the common scripts; see `textsim.fts_words` for what can still differ).
        ``rerank_by_likeness`` additionally reorders the fused top by fuzzy likeness to the
        query (`rank.rerank`: rapidfuzz when installed, else difflib).

        The user query is passed through ``_fts_query``, which strips FTS operator
        characters and ORs the terms. Raw user text reaching an FTS5 MATCH is a syntax
        error waiting to happen — an unbalanced quote or a bare ``-`` raises, and a
        search that throws on a normal question is a search nobody uses.

        ``prefix`` keeps only the rows whose id starts with it (the declared kinds share
        the ``records`` table, ids ``<kind>:<id>``), so one kind is ranked on its own and a
        large one cannot crowd out another.
        """
        cols = _SEARCH_COLS[table]
        pool = max(limit * _POOL, _POOL_MIN)
        terms = _terms(query)
        if not terms:  # nothing to rank on, on every machine
            return []
        with closing(self.connect()) as con:
            self.init(con)
            scan: tuple[list[str], list[dict[str, Any]], list[str]] | None = None

            def scanned() -> tuple[list[str], list[dict[str, Any]], list[str]]:
                nonlocal scan
                if scan is None:
                    rows = [dict(r) for r in con.execute(f"select * from {table}")]  # nosec B608
                    rows = [r for r in rows if str(r.get("id", "")).startswith(prefix)]
                    keys = [_row_key(table, r, n) for n, r in enumerate(rows)]
                    if table == "prompts":  # the id the FTS path gives a prompt, too
                        rows = [{**r, "id": k} for r, k in zip(rows, keys, strict=True)]
                    scan = (keys, rows, [" ".join(str(r.get(c) or "") for c in cols) for r in rows])
                return scan

            def python_rankings(which: str) -> list[str]:
                keys, _, texts = scanned()
                if which == "bm25":
                    score = bm25(
                        [_fallback_words(t) for t in texts],
                        _fallback_words(" ".join(terms)),
                    )
                else:
                    score = substring_bm25(texts, terms)
                return [keys[i] for i in sorted(score, key=lambda i: (-score[i], keys[i]))][:pool]

            lists: list[list[str]] = []
            if self.fts:
                lists = [self._fts_ranking(con, f"{table}_fts", _fts_query(query), pool, prefix)]
                lists.append(
                    self._fts_ranking(con, f"{table}_tri", _tri_query(terms), pool, prefix)
                    if self.trigram
                    else python_rankings("trigram")
                )
            from_fts = any(lists)
            if not from_fts:
                # No FTS5, or it found nothing: both rankers over every row, in Python.
                lists = [python_rankings("bm25"), python_rankings("trigram")]
            top = fuse(lists, pool if rerank_by_likeness else limit)
            if from_fts:
                hits = self._fetch(con, table, top)
            else:
                keys, rows, _ = scanned()
                by_key = dict(zip(keys, rows, strict=True))
                hits = {i: by_key[i] for i in top}
            ordered = [hits[i] for i in top if i in hits]
            if rerank_by_likeness and ordered:
                cands = [
                    (i, " ".join(str(hits[i].get(c) or "") for c in cols)) for i in top if i in hits
                ]
                ordered = [hits[i] for i in rerank(query, cands, limit)]
            return ordered[:limit]

    @staticmethod
    def _fts_ranking(con, index: str, match: str, limit: int, prefix: str = "") -> list[str]:
        """Ids of the FTS5 table ``index`` matching ``match``, best BM25 first; empty for no
        expression, no such table or one FTS5 refuses. ``prefix`` keeps ids that start with it."""
        if not match:
            return []
        try:
            rows = con.execute(
                # bandit B608: `index` is `<key of _SEARCH_COLS>_fts` or `_tri`, built here.
                f"select id from {index} where {index} match ? "  # nosec B608
                f"and substr(id, 1, ?) = ? order by bm25({index}), id limit ?",
                (match, len(prefix), prefix, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["id"] for r in rows]

    @staticmethod
    def _fetch(con, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """The rows for FTS ids. A prompt's id is ``<session>#<role letter><seq>``, which
        names its row by (session, seq) -- the letter says whether the operator spoke or the
        agent noted."""
        out: dict[str, dict[str, Any]] = {}
        if table == "prompts":
            for ident in ids:
                sess, _, tail = ident.partition("#")
                role, digits = (tail[:1], tail[1:]) if tail[:1].isalpha() else ("p", tail)
                seq = _role_seq({"n": "note", "s": "summary"}.get(role, "prompt"), int(digits or 0))
                r = con.execute("select * from prompts where session=? and seq=?", (sess, seq))
                row = r.fetchone()
                if row:
                    out[ident] = {**dict(row), "id": ident}
            return out
        if not ids:
            return out
        ph = ",".join("?" * len(ids))
        # bandit B608: only `?` placeholders are interpolated, and `table` is a key of
        # `_SEARCH_COLS`.
        for r in con.execute(f"select * from {table} where id in ({ph})", ids):  # nosec B608
            out[r["id"]] = dict(r)
        return out

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            self.init(con)
            return [dict(r) for r in con.execute(sql, args)]


def _fts_query(text: str) -> str:
    """Turn arbitrary user text into a safe FTS5 expression.

    Terms are quoted individually and ORed. Quoting is what makes an operator
    character inert; ORing is what makes a multi-word question behave like a
    relevance query instead of a conjunction that matches nothing.
    """
    return " OR ".join(f'"{t}"' for t in _terms(text))


def _tri_query(terms: list[str]) -> str:
    """The FTS5 expression for the trigram index: each term as a quoted substring, ORed.
    Terms under three characters match nothing there, so they are left out."""
    return " OR ".join(f'"{t}"' for t in terms if len(t) >= TRIGRAM_MIN)


def _row_key(table: str, row: dict[str, Any], n: int) -> str:
    """A row's id in the fallback ranking: its ``id``, or a prompt's ``<session>#<letter><seq>``
    -- the FTS id -- so both paths fuse and break ties on the same key."""
    if table != "prompts":
        return str(row.get("id", n))
    role = str(row.get("role") or "prompt")
    return f"{row.get('session', '')}#{role[0]}{_seq_role(role, row.get('seq', 0))}"


#: How many terms of a query reach any ranker.
_FTS_TERMS = 12
#: Each ranker is asked for this many times the wanted hits (at least `_POOL_MIN`) before fusion.
_POOL, _POOL_MIN = 4, 20


def _fallback_words(text: str) -> list[str]:
    """The words the FTS5-less fallback ranks on: every word, lower-cased and stemmed the way
    FTS5's porter tokenizer stems it, so a query returns the same rows without FTS5."""
    return [porter.stem(w) for w in textsim.fts_words(text)]


def _terms(query: str) -> list[str]:
    """The terms every ranker is asked for, on every machine: the query's words of at least
    ``MIN_TERM_CHARS``, the first ``_FTS_TERMS`` of them."""
    return textsim.words(query, min_len=MIN_TERM_CHARS)[:_FTS_TERMS]


#: What `recall` searches, in the order a reader should weigh them. Decisions first
#: because they are binding, lessons next because they are transferable, then evidence,
#: then history. The order is the answer to "which of these should change what I do".
RECALL_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("decisions", "DECISION", "binding — follow it unless the operator says otherwise"),
    ("lessons", "LESSON", "learned the hard way here"),
    ("memories", "MEMORY", "an operational fact about this machine or repository"),
    ("research", "RESEARCH", "already investigated; check the verdict before redoing it"),
    ("bugs", "BUG", "this has broken before"),
    ("items", "TASK", "similar work already planned or done"),
    ("prompts", "PROMPT/NOTE", "the operator asked, or an agent recorded, something like this"),
)


def recall_sources() -> tuple[tuple[str, str, str], ...]:
    """What recall searches now: the fixed sources, then each declared kind (its name is its
    table, its label the name in capitals, its hint the descriptor's ``recall_hint``)."""
    return RECALL_SOURCES + PA.recall_entries()


def _fit(text: str, width: int) -> str:
    """The first ``width`` characters of a hit's body: no marker, no trimming."""
    return clip(text, width, rstrip=False)


def summarise_row(table: str, row: dict[str, Any], width: int = 240) -> tuple[str, str]:
    """(headline, body) for one hit, per source table."""
    if table == "decisions":
        head = f"{row.get('title', '')}"
        if row.get("status") != "accepted" or row.get("superseded_by"):
            head += f"  [{row.get('status')}"
            head += f" -> {row['superseded_by']}]" if row.get("superseded_by") else "]"
        return head, _fit(row.get("decision") or "", width)
    if table == "lessons":
        return row.get("title", ""), _fit(row.get("rule") or "", width)
    if table == "research":
        return (
            f"{row.get('question', '')}  [{row.get('verdict', '')}]",
            _fit(row.get("claim") or "", width),
        )
    if table == "bugs":
        # Three states, never two: a false finding labelled "fixed" would tell the next
        # agent a repair exists, and one labelled "OPEN" would send it to fix nothing.
        if row.get("fixed_at"):
            return f"{row.get('summary', '')}  [fixed]", _fit(row.get("lesson") or "", width)
        if row.get("invalid_at"):
            why = f"invalid: {row.get('invalid_reason') or ''}"
            if row.get("evidence"):
                why += f" (evidence: {row['evidence']})"
            return f"{row.get('summary', '')}  [invalid]", _fit(why, width)
        return f"{row.get('summary', '')}  [OPEN]", _fit(row.get("lesson") or "", width)
    if table == "items":
        return f"{row.get('id', '')} — {row.get('title', '')}", _fit(row.get("body") or "", width)
    if table == "memories":
        when = (row.get("origin_at") or row.get("at") or "")[:10]
        return f"{when} {row.get('id', '')}".strip(), _fit(row.get("text") or "", width)
    if table == "prompts":
        text = (row.get("text") or "").strip().replace("\n", " ")
        # A note is the AGENT's record of the work — a dead end, a surprise, why it
        # changed approach — and attributing one to the operator would put words in
        # their mouth, which is worse than not surfacing it at all.
        who = {
            "note": "the agent noted:",
            "summary": f"SESSION SUMMARY ({row.get('session', '')}):",
        }.get(row.get("role") or "", "operator asked:")
        return f"{row.get('at', '')[:10]} {who}", _fit(text, width)
    if table in PA.DECLARED:
        status = row.get("status") or ""
        tag = f"  [{status}]" if status and status != "active" else ""
        return f"{row.get('title', '')}{tag}", _fit(row.get("body") or "", width)
    return row.get("id", ""), ""
