"""Bug near-duplicate matching: symptom + root cause, by overlap and semantics.

The `ddflow similar` engine (`services.similar`) already weighs records by the TF-IDF
cosine of their content words (`core.textsim`): two reports of one defect keep the same
root-cause words -- file, lease, deadlock, queue -- and score high however differently
they are worded, while an opaque id or a git sha shared by two unrelated bugs is masked.
That is overlap matching, not literal token equality.

This module adds the SEMANTIC half on top, reusing the project's one embedder
(`services.embed`, D-unify 2) so two paraphrases that share NO word of the other still
cluster. The combined rule the sweep and the strengthenings share is one line: a pair
clusters when EITHER signal reaches ``floor``:

* overlap (`services.similar`) always runs -- deterministic, no model;
* semantic (`services.embed`) runs only when `[rag]` names an embedder, and the answer
  says so (`note`) when it could not.

Nothing here closes a bug; it only scores and links (``link_record`` does the linking).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import textsim
from . import embed
from . import similar as sim

#: Distinct content words of the shared cause to show beside a cluster: enough to read
#: why the members were grouped, few enough to scan.
CAUSE_WORDS = 10


def record_text(rec: Mapping[str, Any]) -> str:
    """The text a matcher reads: the title and the body, as `similar_records` projects them."""
    return " ".join(
        x for x in (" ".join(str(rec.get("title") or "").split()), str(rec.get("body") or "")) if x
    )


def embed_vectors(
    repo: Path, cfg: Config, texts: Sequence[str]
) -> tuple[list[tuple[float, ...]] | None, str]:
    """Embed ``texts`` (one per record, in order) or ``(None, note)`` when the embedder
    cannot answer. The note names why, so a caller never has to guess whether the semantic
    half ran. Reused by the sweep, `ddflow similar` and the `bug found` add check."""
    if not texts:
        return [], ""
    got = embed.embed(repo, cfg, list(texts))
    if got.unavailable:
        return None, f"semantic embedding unavailable ({got.reason}); overlap matcher used"
    return list(got.vectors), ""


def semantic_hits(
    repo: Path,
    cfg: Config,
    query: str,
    records: Sequence[Mapping[str, Any]],
    *,
    floor: float,
    limit: int = 0,
) -> tuple[list[tuple[str, float]], str]:
    """``[(id, score), ...]`` of the records whose EMBEDDING cosine with ``query`` reaches
    ``floor``, best first, and a note ("" when it ran). The ids are the records' own, so the
    caller maps them back to a kind/state it already holds."""
    texts = [record_text(r) for r in records]
    if not texts:
        return [], ""
    got = embed.embed(repo, cfg, [query, *texts])
    if got.unavailable:
        return [], f"semantic embedding unavailable ({got.reason})"
    q = got.vectors[0]
    scored = [
        (str(rec["id"]), embed.cosine(q, v))
        for rec, v in zip(records, got.vectors[1:], strict=True)
    ]
    scored = [(i, s) for i, s in scored if s >= floor]
    scored.sort(key=lambda h: (-h[1], h[0]))
    return scored[:limit] if limit else scored, ""


def shared_cause(records: Sequence[Mapping[str, Any]], ids: Sequence[str]) -> str:
    """The content words every member of a cluster shares (the symptom/root-cause in
    common), in the canonical's order, capped. "" when none is common -- a cluster held
    together by semantics alone has its cause in the canonical's own headline, not here."""
    by = {str(r["id"]): r for r in records}
    sets = []
    for rid in ids:
        rec = by.get(rid)
        if rec is None:
            return ""
        toks = textsim.tokens(rec.get("title") or "", rec.get("body") or "")
        sets.append([t for t in dict.fromkeys(toks) if not t.startswith("--")])
    if not sets:
        return ""
    first = sets[0]
    common = [t for t in first if all(t in s for s in sets[1:])]
    return " ".join(common[:CAUSE_WORDS])


#: Two bugs make a cluster.
_MIN_CLUSTER = 2


def cluster(
    records: Sequence[Mapping[str, Any]],
    *,
    floor: float,
    vectors: Sequence[Sequence[float]] | None = None,
) -> tuple[list[list[str]], dict[tuple[str, str], float]]:
    """Group ``records`` (id, title, body) into the maximal sets connected by a pair
    scoring at least ``floor`` on EITHER the TF-IDF overlap (always) or the semantic cosine
    (``vectors``, one per record in order). Returns (groups, scores): groups of 2+ ids in
    first-occurrence order (the caller keeps its records oldest-first, so the head of a
    group is its canonical member), and every scored pair's best score keyed by sorted ids.

    Pure in ``records`` and ``vectors``: no store, no network. The embedder is elsewhere
    (`embed_vectors`); a caller that passed ``vectors=None`` has only the overlap signal.
    """
    ids = [str(r["id"]) for r in records]
    n = len(ids)
    if n < _MIN_CLUSTER:
        return [], {}
    scores = _overlap_scores(records)
    if vectors is not None:
        _merge_semantic(scores, ids, vectors)
    return [m for m in _components(ids, scores, floor) if len(m) >= _MIN_CLUSTER], scores


def _overlap_scores(
    records: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """Every pair the TF-IDF index scores above zero, keyed canonically, best score kept."""
    index = sim.build(records)
    scores: dict[tuple[str, str], float] = {}
    for r in records:
        for other, s in index.query(r, limit=None):
            if other == str(r["id"]) or s <= 0.0:
                continue
            k = _key(str(r["id"]), other)
            scores[k] = max(scores.get(k, 0.0), s)
    return scores


def _merge_semantic(
    scores: dict[tuple[str, str], float],
    ids: Sequence[str],
    vectors: Sequence[Sequence[float]],
) -> None:
    """Fold each pair's semantic cosine into ``scores`` (its best of the two signals)."""
    vs = [tuple(v) for v in vectors]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            s = embed.cosine(vs[i], vs[j])
            if s > 0.0:
                k = _key(ids[i], ids[j])
                scores[k] = max(scores.get(k, 0.0), s)


def _components(
    ids: Sequence[str], scores: Mapping[tuple[str, str], float], floor: float
) -> list[list[str]]:
    """The connected components over pairs scoring at least ``floor``, in first-occurrence
    order, each as the ids it holds (singletons included; the caller drops them)."""
    pos = {rid: i for i, rid in enumerate(ids)}
    parent = list(range(len(ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), s in scores.items():
        if s >= floor:
            ra, rb = find(pos[a]), find(pos[b])
            if ra != rb:
                parent[rb] = ra
    grouped: dict[int, list[str]] = {}
    order: list[int] = []
    for i, rid in enumerate(ids):
        root = find(i)
        if root not in grouped:
            grouped[root] = []
            order.append(root)
        grouped[root].append(rid)
    return [grouped[r] for r in order]


def _key(a: str, b: str) -> tuple[str, str]:
    """The canonical key of an unordered pair, by id string, as `cluster` documents."""
    return (a, b) if a < b else (b, a)
