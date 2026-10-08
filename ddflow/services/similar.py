"""Which existing records is this new one like? The no-LLM similarity engine.

Decision D-no-duplicates; research R-dedupe-matchers (the method) and R-similar-exact
(why it is exact rather than FTS5-shortlisted). Every add is to be checked against the
records already in the log (B-add-checks-duplicates wires that in); this module answers
the question underneath: given a record's kind, title and body, which records score how
close, and does the configured policy say to show them, ask about them, or say nothing.

Two matchers, one contract (``Matcher``):

* ``build(records)`` -> ``Index``, in memory, from records in hand;
* ``open_store(store)`` -> ``StoreIndex``, over the weights ``Store.rebuild`` projected
  into ``index.db`` -- the add path's matcher, which does not re-read the log or
  re-tokenize thousands of records to weigh one.

Both score the same way: the TF-IDF cosine (``core.textsim``) of the query with every
record that shares a term with it, walked through an inverted index. That is exact --
a record missing from the hits shares no term and scores 0 -- so the two matchers
return the same scores for the same corpus, and neither depends on how SQLite was
built. (The research's first design shortlisted 50 candidates per kind with FTS5's BM25
and re-ranked them; on run_nemo_run's 6,200 records that changed the top three above
the show floor for 16 of 300 queries, and was no faster than the inverted index read
from ``index.db``: median 0.8 ms, p95 5.6 ms. So FTS5 is not used here, and there is no
"without FTS5" path to differ from.)

Candidates cross kinds: a bug sees the open task that fixes it, a task sees the bug it
would fix. ``Matcher`` is the seam an embeddings backend (B-semantic-recall) plugs into:
anything with ``query``, ``doc`` and ``ids`` can stand behind ``assess``.
"""

from __future__ import annotations

import re
import sqlite3
from array import array
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import Config
from ..core import textsim
from ..infra.store import Store, similar_records

#: What ``assess`` decided. ``off``: not checked (on_match off, or a kind not in
#: [dedupe].kinds). ``none``: nothing reached the show floor. ``show``: candidates to
#: list, nothing to ask. ``ask``: an answer is needed before the add proceeds. ``warn``:
#: what would have been ``ask``, under ``on_match = "warn"``.
ACTIONS = ("off", "none", "show", "ask", "warn")

#: A token that could be a record id named in the text: ``B5c32cbb5c1``, ``B198``,
#: ``B-similar-engine``, ``34.8g``. Only ids the matcher knows count, and only ids with a
#: digit or a separator in them: an imported project can have an item called ``cleanup``,
#: and every text using that word would otherwise "name" it.
_NAMED = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")
_ID_SHAPE = re.compile(r".*(?:\d|[-_.]).*")


@dataclass(frozen=True)
class Doc:
    """What a matcher knows about an indexed record besides its weights."""

    id: str
    kind: str
    item: str = ""
    digest: str = ""


class Matcher(Protocol):
    """A similarity backend. Scores are in [0, 1], best first, ties by id."""

    def query(
        self,
        record: Mapping[str, Any],
        limit: int | None = None,
        kinds: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]: ...

    def doc(self, rid: str) -> Doc | None: ...

    def ids(self) -> frozenset[str]: ...

    def doc_freq(self, terms: Iterable[str]) -> tuple[int, dict[str, int]]: ...


def record_tokens(record: Mapping[str, Any]) -> list[str]:
    return textsim.tokens(str(record.get("title") or ""), str(record.get("body") or ""))


class _Exact:
    """The scoring both matchers share: exact TF-IDF cosine over an inverted index."""

    _docs: list[Doc]
    _n: int

    def _postings(self, terms: list[str]) -> dict[str, textsim.Postings]:
        raise NotImplementedError

    def query(
        self,
        record: Mapping[str, Any],
        limit: int | None = None,
        kinds: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Records scoring above zero against ``record`` (a mapping with title and
        body), best first. ``limit=None`` returns every one; ``kinds`` keeps only
        records of those kinds. The record itself, if indexed, may be among them."""
        # Rounded to 9 places: a record's own text sums to 0.9999999999999997, and an
        # identical record must read 1.0. Both matchers round alike.
        toks = record_tokens(record)
        if not toks or not self._n:
            return []
        post = self._postings(list(dict.fromkeys(toks)))
        df = {t: len(p[0]) for t, p in post.items()}
        acc = textsim.cosine(textsim.vector(toks, df, self._n), post)
        wanted = frozenset(kinds) if kinds is not None else None
        hits = []
        for i, raw in acc.items():
            # The filter sees the ROUNDED score, so a returned hit never reads 0.0 (the
            # contract says "above zero"). Defensive: the smoothed IDF is >= 1, so a
            # cosine this small needs a hundred-thousand-term record and none exists.
            score = min(1.0, round(raw, 9))
            if score > 0.0 and (wanted is None or self._docs[i].kind in wanted):
                hits.append((self._docs[i].id, score))
        hits.sort(key=lambda h: (-h[1], h[0]))
        return hits if limit is None else hits[:limit]

    def doc(self, rid: str) -> Doc | None:
        return self._by_id().get(rid)

    def ids(self) -> frozenset[str]:
        return frozenset(self._by_id())

    def doc_freq(self, terms: Iterable[str]) -> tuple[int, dict[str, int]]:
        """(records indexed, how many hold each of ``terms``): the postings' lengths,
        so a caller can weigh words without re-tokenizing the corpus."""
        post = self._postings(list(dict.fromkeys(terms)))
        return self._n, {t: len(p[0]) for t, p in post.items()}

    def _by_id(self) -> dict[str, Doc]:
        by = getattr(self, "_by_id_cache", None)
        if by is None:
            by = self._by_id_cache = {d.id: d for d in self._docs}
        return by


class Index(_Exact):
    """A matcher over records in memory."""

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        recs = list(records)
        self._docs = [
            Doc(
                id=str(r["id"]),
                kind=str(r.get("kind") or ""),
                item=str(r.get("item") or ""),
                digest=textsim.digest(str(r.get("title") or ""), str(r.get("body") or "")),
            )
            for r in recs
        ]
        self._n = len(recs)
        _df, self._post = textsim.invert([record_tokens(r) for r in recs])

    def _postings(self, terms: list[str]) -> dict[str, textsim.Postings]:
        return {t: self._post[t] for t in terms if t in self._post}


def build(records: Iterable[Mapping[str, Any]]) -> Index:
    """An in-memory matcher over ``records``: mappings with id, kind, title, body and,
    optionally, item (what a bug was filed against, a task's phase)."""
    return Index(records)


class StoreIndex(_Exact):
    """A matcher over the weights ``Store.rebuild`` wrote into ``index.db``.

    A SNAPSHOT: it reads the record table once (a few ms at 6k records) and only the
    postings of the query's own terms per query, from the file as it was when opened --
    ``Store.rebuild`` swaps a new file in and never edits the old one, so an instance
    that outlives a rebuild answers consistently from the older index. Open one per
    check, after ``store.ensure(log)``. Opened read-only: the store's rebuild is the only
    writer, and a reader must not take the database's write lock."""

    def __init__(self, path: Path) -> None:
        self._con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        meta = dict(self._con.execute("select k, v from meta").fetchall())
        if meta.get("similar_version") != str(textsim.VERSION):
            self._con.close()
            raise LookupError(
                f"{path} holds similarity weights of version "
                f"{meta.get('similar_version')!r}, not {textsim.VERSION}: rebuild it"
            )
        self._n = int(meta.get("similar_n") or 0)
        rows = self._con.execute(
            "select ord, id, kind, item, digest from similar_docs order by ord"
        ).fetchall()
        self._docs = [Doc(id=r[1], kind=r[2], item=r[3], digest=r[4]) for r in rows]
        if len(self._docs) != self._n or any(r[0] != i for i, r in enumerate(rows)):
            self._con.close()
            raise LookupError(f"{path}: similarity tables are inconsistent: rebuild it")

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> StoreIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _postings(self, terms: list[str]) -> dict[str, textsim.Postings]:
        out: dict[str, textsim.Postings] = {}
        # In chunks: SQLite caps bound parameters (999 on older builds), and a long
        # body has more distinct terms than that.
        for k in range(0, len(terms), 500):
            chunk = terms[k : k + 500]
            ph = ",".join("?" * len(chunk))
            # bandit B608: only `?` placeholders are interpolated.
            sql = f"select term, docs, weights from similar_terms where term in ({ph})"  # nosec B608
            for term, docs, weights in self._con.execute(sql, chunk):
                d, w = array("q"), array("d")
                d.frombytes(docs)
                w.frombytes(weights)
                out[term] = (d, w)
        return out


def open_store(store: Store) -> StoreIndex:
    """The matcher over ``store``'s index. The caller makes the index current first
    (``store.ensure(log)``); a missing or outdated one raises ``LookupError``, never
    answers from stale weights."""
    if not store.path.exists():
        raise LookupError(f"{store.path} does not exist: rebuild the index")
    try:
        return StoreIndex(store.path)
    except sqlite3.DatabaseError as exc:
        raise LookupError(f"{store.path}: {exc}: rebuild the index") from exc


# ------------------------------------------------------------------------- the policy


@dataclass(frozen=True)
class Candidate:
    """One existing record shown to whoever is adding. ``flags``: ``identical`` (the
    same text up to case and whitespace), ``named`` (the new text names its id),
    ``same_item`` (filed against the same item, or the item itself), ``filed_against``
    (the new record's item IS this one: it asks nothing)."""

    id: str
    kind: str
    score: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assessment:
    action: str  # one of ACTIONS
    candidates: list[Candidate] = field(default_factory=list)
    #: distinct content words in the new record -- the ask rule's minimum is on these
    words: int = 0

    @property
    def identical(self) -> Candidate | None:
        return next((c for c in self.candidates if "identical" in c.flags), None)


def _asks(c: Candidate, kind: str, words: int, dd) -> bool:
    """Does this candidate make the add ask? An identical record of the SAME kind always
    does (it is merged, not asked about, but it is the strongest match there is); a record
    the new one is filed AGAINST asks nothing -- a bug with ``--item T`` already says T is
    what fixes it -- even when the texts are identical; otherwise a named id or a score at
    the ask threshold with enough words."""
    if "identical" in c.flags and c.kind == kind:
        return True
    if "filed_against" in c.flags:
        return False
    return (
        "identical" in c.flags
        or "named" in c.flags
        or (c.score >= dd.ask_threshold and words >= dd.min_words)
    )


def assess(matcher: Matcher, record: Mapping[str, Any], cfg: Config) -> Assessment:
    """Apply ``[dedupe]`` to one record about to be added.

    Shown: the best ``max_candidates`` scoring at least ``show_floor``, plus any record
    whose id the text names (always, whatever it scores). Asked: when a candidate
    scores at least ``ask_threshold`` and the record has at least ``min_words`` content
    words -- a two-word title matches everything, and asking on it is noise -- or when
    the text names an existing id, or is identical to an existing record.
    ``on_match = "warn"`` turns the ask into a warning; ``"off"`` skips the check.
    Candidates come from every kind in ``[dedupe].kinds``, not only the record's own.
    """
    dd = cfg.dedupe
    kind = str(record.get("kind") or "")
    if dd.on_match == "off" or kind not in dd.kinds:
        return Assessment("off")
    own = str(record.get("id") or "")
    item = str(record.get("item") or "")
    words = textsim.content_words(record_tokens(record))
    text = f"{record.get('title') or ''} {record.get('body') or ''}"
    known = matcher.ids()
    named = [
        t
        for t in dict.fromkeys(_NAMED.findall(text))
        if t in known and t != own and _ID_SHAPE.fullmatch(t)
    ]
    want = textsim.digest(str(record.get("title") or ""), str(record.get("body") or ""))
    hits = [(i, s) for i, s in matcher.query(record, limit=None, kinds=dd.kinds) if i != own]
    scores = dict(hits)
    chosen = [i for i, s in hits if s >= dd.show_floor][: dd.max_candidates]
    chosen += [i for i in named if i not in chosen]
    out: list[Candidate] = []
    for i in chosen:
        d = matcher.doc(i)
        if d is None:
            continue
        flags = []
        if d.digest == want:
            flags.append("identical")
        if i in named:
            flags.append("named")
        if item and item in (d.item, d.id):
            flags.append("same_item")
        if item and item == d.id:
            flags.append("filed_against")
        out.append(Candidate(i, d.kind, scores.get(i, 0.0), tuple(flags)))
    if not out:
        return Assessment("none", [], words)
    # A record filed AGAINST the candidate (a bug with --item T, T being the task that
    # fixes it) is already answered: it is shown, and it asks nothing.
    asked = any(_asks(c, kind, words, dd) for c in out)
    if not asked:
        return Assessment("show", out, words)
    return Assessment("ask" if dd.on_match == "ask" else "warn", out, words)


def is_duplicate(candidate: Candidate, assessment: Assessment, cfg: Config) -> bool:
    """The rule an add uses: an identical record, or a near match with enough content
    words (`[dedupe]`). ONE implementation, shared by the import path and the add check,
    so a threshold change cannot land in one and miss the other (dedupe on 5c9fc38)."""
    dd = cfg.dedupe
    return "identical" in candidate.flags or (
        candidate.score >= dd.ask_threshold and assessment.words >= dd.min_words
    )


# ------------------------------------------------------------------------- the one screen

#: Marks a record of the batch being screened in the index, so it never shares an id with
#: a stored one (a bug or an item can carry the same word).
_IN_BATCH = "\x00batch:"


@dataclass
class Screened:
    """A screened record that repeats an existing one: ``index`` is its position in the
    batch, ``of`` the id it repeats, ``where`` ``queue`` (already in the log) or
    ``import`` (another record of the same batch)."""

    index: int
    of: str
    score: float
    identical: bool = False
    where: str = "queue"


def same_text(text: str) -> str:
    """``text`` folded for an exact-copy test: case and whitespace do not count."""
    return " ".join(text.casefold().split())


def screen(
    records: Iterable[Mapping[str, Any]],
    state: Any,
    cfg: Config,
    *,
    compare: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
    fold_copies: bool = False,
) -> tuple[list[Mapping[str, Any]], list[Screened]]:
    """Split a batch of records about to be added into ``(kept, repeats)`` -- the ONE
    screen an import applies (D-no-duplicates; the same engine and `[dedupe]` thresholds
    an add uses, `assess` and `is_duplicate`).

    Each record is weighed against every record in ``state`` (None: an empty queue) and
    left out when one is a duplicate. Records of the batch are not compared with each
    other unless asked: ``compare(record, target)`` says which pairs are -- a record is
    then also weighed against the batch members it admits (the importer's rule: only a
    summary-born lesson, against a record that is not) -- and ``fold_copies`` drops the
    later of two batch records with the same text (case and whitespace aside), which is
    what the harness memory import wants. Which of two near records to keep is otherwise
    the author's call, and dropping the later would make the outcome depend on the order
    the files were read. ``repeats`` is in batch order. With ``[dedupe] on_match = "off"``
    or a kind not in ``[dedupe].kinds`` nothing is left out (`assess` says ``off``), copies
    of a batch member included.
    """
    batch = list(records)
    base = similar_records(state) if state is not None else []
    if compare is None:
        index = build(base)
        probes = batch
    else:
        probes = [{**r, "id": _IN_BATCH + str(r["id"])} for r in batch]
        index = build([*base, *probes])
    peers = {str(p["id"]): i for i, p in enumerate(probes)} if compare else {}
    repeats: list[Screened] = []
    seen: dict[str, str] = {}
    for i, probe in enumerate(probes):
        hit = _repeat_of(i, probe, index, cfg, batch, peers, compare)
        if hit is None and fold_copies and _screened(cfg, batch[i]):
            hit = _copy_of(i, batch[i], seen)
        if hit is not None:
            repeats.append(hit)
    dropped = {h.index for h in repeats}
    return [r for i, r in enumerate(batch) if i not in dropped], repeats


def _copy_of(i: int, rec: Mapping[str, Any], seen: dict[str, str]) -> Screened | None:
    """``rec`` as a word-for-word copy of an earlier batch member (kept in ``seen``), or
    None -- and None for a record with no text, which is a copy of nothing."""
    key = same_text(str(rec.get("body") or ""))
    if not key:
        return None
    if key in seen:
        return Screened(i, seen[key], 1.0, True, "import")
    seen[key] = str(rec["id"])
    return None


def _screened(cfg: Config, rec: Mapping[str, Any]) -> bool:
    """Whether `[dedupe]` checks this record at all (``assess`` says ``off`` otherwise)."""
    return cfg.dedupe.on_match != "off" and str(rec.get("kind") or "") in cfg.dedupe.kinds


def _repeat_of(
    i: int,
    probe: Mapping[str, Any],
    index: Matcher,
    cfg: Config,
    batch: list[Mapping[str, Any]],
    peers: dict[str, int],
    compare: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None,
) -> Screened | None:
    """The first existing record, or batch member ``compare`` admits, that ``probe``
    repeats under `[dedupe]` (`is_duplicate`), or None."""
    a = assess(index, probe, cfg)
    for c in a.candidates:
        peer = peers.get(c.id)
        if peer is not None and not (compare and compare(batch[i], batch[peer])):
            continue
        if is_duplicate(c, a, cfg):
            of = str(batch[peer]["id"]) if peer is not None else c.id
            return Screened(
                i, of, c.score, "identical" in c.flags, "queue" if peer is None else "import"
            )
    return None
