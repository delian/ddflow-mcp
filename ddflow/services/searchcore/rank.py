"""The rankers: BM25, reciprocal-rank fusion and the TF-IDF cosine duplicate detection uses.

Pure functions over token lists, so any source ranks the same way. FTS5's own BM25 stays in
`infra/store.py` (it runs inside SQLite); `rrf` fuses its list with any other.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ...core import textsim

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60


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
