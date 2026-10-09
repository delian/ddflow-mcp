"""The rankers: BM25, reciprocal-rank fusion and the TF-IDF cosine duplicate detection uses.

Pure functions over token lists, so any source ranks the same way. FTS5's own BM25 stays in
`infra/store.py` (it runs inside SQLite); `rrf` fuses its list with any other.

``substring_bm25`` is the trigram ranker where SQLite has no trigram tokenizer: a doc matches
a term exactly when FTS5's trigram index would match it (the term occurs in the text, any
case), so the candidate set is the same on every machine (the scores approximate its BM25,
which weighs trigrams, not whole terms). ``rerank`` is the optional fuzzy
step over a fused list: rapidfuzz when the ``[search]`` extra is installed, ``difflib``
otherwise (`core/fuzzy`, the extra's one adapter).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from . import fuzzy, textsim

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60
#: The shortest term a trigram index can match.
TRIGRAM_MIN = 3


def bm25(docs: Sequence[Sequence[str]], query: Iterable[str]) -> dict[int, float]:
    """BM25 (k1=1.5, b=0.75) of the distinct `query` tokens against each tokenised doc;
    index -> score, zero scores left out."""
    q = set(query)
    if not q or not docs:
        return {}
    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q}
    out: dict[int, float] = {}
    for i, d in enumerate(docs):
        score = 0.0
        for t in q:
            tf = d.count(t)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += (
                idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * ((1 - BM25_B) + BM25_B * len(d) / avg))
            )
        if score > 0:
            out[i] = score
    return out


def tfidf(docs: Sequence[Sequence[str]], query: Sequence[str]) -> dict[int, float]:
    """Cosine of the query against each tokenised doc (`core/textsim`); a doc sharing no term
    with the query is absent (`cosine` only scores docs that share one)."""
    df, post = textsim.invert(docs)
    scores = textsim.cosine(textsim.vector(query, df, max(1, len(docs))), post)
    return dict(scores)


def rrf(rankings: Iterable[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal-rank fusion: id -> sum of 1/(k + rank) over the best-first `rankings`."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, ident in enumerate(ranking, start=1):
            fused[ident] = fused.get(ident, 0.0) + 1.0 / (k + rank)
    return fused


def _occurrences(text: str, term: str) -> int:
    """How often ``term`` occurs in ``text``, overlaps included (``aba`` twice in ``ababa``),
    as a trigram index counts them."""
    n, at = 0, text.find(term)
    while at >= 0:
        n += 1
        at = text.find(term, at + 1)
    return n


def substring_bm25(texts: Sequence[str], terms: Iterable[str]) -> dict[int, float]:
    """BM25 of each distinct lower-cased ``terms`` entry's occurrences as a SUBSTRING of each
    text (the question FTS5's trigram tokenizer answers); index -> score, non-matches left
    out. Terms under three characters match nothing, as in the trigram index."""
    q = {t.lower() for t in terms if len(t) >= TRIGRAM_MIN}
    if not q or not texts:
        return {}
    low = [t.lower() for t in texts]
    n = len(low)
    avg = (sum(len(t) for t in low) / n) or 1.0
    counts = [{t: _occurrences(text, t) for t in q} for text in low]
    df = {t: sum(1 for c in counts if c[t]) for t in q}
    out: dict[int, float] = {}
    for i, text in enumerate(low):
        score = 0.0
        for t in q:
            tf = counts[i][t]
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += (
                idf
                * tf
                * (BM25_K1 + 1)
                / (tf + BM25_K1 * ((1 - BM25_B) + BM25_B * len(text) / avg))
            )
        if score > 0:
            out[i] = score
    return out


def fuse(rankings: Iterable[Sequence[str]], limit: int, k: int = RRF_K) -> list[str]:
    """The ids of the best-first ``rankings``, fused by `rrf`, best first, at most ``limit``;
    equal scores keep the id order so the result never depends on dict iteration."""
    fused = rrf(rankings, k)
    return sorted(fused, key=lambda i: (-fused[i], i))[:limit]


def rerank(query: str, candidates: Sequence[tuple[str, str]], limit: int) -> list[str]:
    """Reorder ``candidates`` (id, text), already best first, by fuzzy likeness to ``query``,
    keeping the incoming order among equal scores. The fused order is the relevance signal;
    this only lifts the text a person would call closest."""
    scored = [
        (-fuzzy.ratio(query, text), pos, ident) for pos, (ident, text) in enumerate(candidates)
    ]
    return [ident for _, _, ident in sorted(scored)][:limit]
