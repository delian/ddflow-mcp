"""Text analysis for duplicate detection: pure, stdlib, deterministic.

Decision D-no-duplicates, research R-dedupe-matchers. A record's text becomes a bag of
content words; words become TF-IDF weights; two records are as similar as the cosine of
their weight vectors. The research measured this against NLTK, rapidfuzz, difflib,
MinHash and scikit-learn on ddflow's own labelled duplicates and none did better, so the
whole method is the hundred lines below -- no model, no dependency.

Pure because two layers need it: ``infra.store`` projects every record's weights into
``index.db`` when it rebuilds, and ``services.similar`` weighs the text being added
against them. One tokenizer for both is what makes a stored vector and a fresh one
comparable at all.

What the tokenizer does, and why each step is there:

* **Code-aware splitting.** Records here are about code, so ``adopt_existing``,
  ``Store.rebuild``, ``ddflow/infra/store.py`` and ``inferWorktree`` are split into
  their words (``adopt``, ``existing``, ``store``, ``rebuild``, ...): a bug that says
  "adopts the existing worktree" and one that names ``adopt_existing`` are about the
  same thing. Paths and dotted names keep every component. A long flag is kept whole as
  well (``--no-worktree``): a flag names one CLI surface, which its separate words do
  not -- ``no`` is a stop word.
* **Masking what identifies an instance rather than a defect.** Record ids
  (``B5c32cbb5c1``), git shas and other opaque runs -- session ids, harness worktree
  hashes (``015NbhRCT5Xtid...``) -- are dropped. They are rare, so TF-IDF would weight
  them highest, and what they share is *where* something was seen: two different bugs
  reported from one session share its id. A run counts as opaque when it is 10+
  characters mixing letters and digits, or a 7-40 character hex string with a digit
  (``defaced`` is a word; the old all-hex rule masked it). ``Imported from <file>.``
  stubs are boilerplate, not content.
* **A light suffix stemmer**, so ``claims``/``claimed``/``claiming`` meet. Porter (via
  NLTK, or FTS5's) measured no better on the labelled set.
* **Stop words** carry nothing a duplicate could share.

Changing any of this changes every stored vector: bump ``VERSION`` so ``index.db``
re-derives them.
"""

from __future__ import annotations

import functools
import math
import re
import unicodedata
from array import array
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence

from .digest import content_digest, normalize_text

#: Bumped whenever ``tokens``, the weighting or the record ``digest`` changes (2: redaction
#: markers are one token in it); ``index.db`` stores it.
VERSION = 2

STOP = frozenset(
    """a an the and or but if then else of to in on at by for with from as is are was were
    be been being it its this that these those there here not no nor so than too very can
    could should would will shall may might must do does did done has have had having which
    who whom whose what when where why how all any both each few more most other some such
    only own same just also into over under again further once out up down off about above
    below between through during before after while because until we you he she they them
    our your their his her my me us one two via per vs eg ie etc""".split()
)
#: Words this short are left unstemmed, and a stem keeps at least this many letters.
_STEM_MIN_WORD, _STEM_MIN_ROOT = 4, 3
#: A run mixing letters and digits this long is opaque (a session id, a hash).
_OPAQUE_MIN = 10
#: Hex runs of these lengths, with a digit, are git shas (short to full).
_SHA_MIN, _SHA_MAX = 7, 40
#: An auto record id: one capital letter and ten hex digits (`core.ids.auto_id`).
_ID_LEN = 11
#: Shortest word kept.
_MIN_WORD = 2
_SUFFIXES = ("ations", "ation", "ings", "ing", "edly", "ed", "ies", "es", "ly", "s")

_RUN = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_FLAG = re.compile(r"(?<![\w/.-])--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_IMPORTED = re.compile(r"Imported from \S+\.")
_HEX = frozenset("0123456789abcdef")


def stem(word: str) -> str:
    """Strip one common English suffix; words of four letters or fewer are left alone."""
    if len(word) <= _STEM_MIN_WORD:
        return word
    for s in _SUFFIXES:
        if word.endswith(s) and len(word) - len(s) >= _STEM_MIN_ROOT:
            return word[: -len(s)] + ("y" if s == "ies" else "")
    return word


@functools.lru_cache(maxsize=1 << 16)
def _run(run: str) -> tuple[str, ...]:
    """The tokens one alphanumeric run contributes. Cached: a corpus repeats its words,
    and this is where a rebuild of a few thousand records spends its time."""
    n = len(run)
    if not run.isalpha():
        if n >= _OPAQUE_MIN and not run.isdigit():
            return ()  # opaque: a session id, a worktree hash
        if _SHA_MIN <= n <= _SHA_MAX and set(run) <= _HEX:
            return ()  # a git sha
    elif n == _ID_LEN and run[0].isupper() and set(run[1:]) <= _HEX:
        return ()  # a record id without a digit in it
    run = run.lstrip("0123456789")
    if len(run) < _MIN_WORD:
        return ()
    if run.islower() or run.isupper() or run[1:].islower():
        parts: Sequence[str] = (run.lower(),)
    else:
        parts = _CAMEL.sub(r"\1 \2", run).lower().split()
    return tuple(stem(p) for p in parts if len(p) >= _MIN_WORD and p not in STOP)


#: Shortest word `words` keeps by default: one character matches almost everything and
#: ranks nothing.
MIN_WORD_CHARS = 2
_WORD = re.compile(r"\w+")
_WORD_HYPHEN = re.compile(r"[\w-]+")
_FTS_WORD = re.compile(r"[^\W_]+")


def words(
    text: str, *, min_len: int = MIN_WORD_CHARS, hyphens: bool = False, fold: bool = False
) -> list[str]:
    """The plain words of ``text``, in order: maximal runs of word characters, at least
    ``min_len`` long. No stemming and no stop words -- these are the words a person typed,
    for matching them literally (an FTS5 or LIKE query, a Jaccard overlap), where ``tokens``
    is the normalised form for weighing similarity. ``hyphens`` keeps ``-`` inside a word
    (``foo-bar`` is one), ``fold`` lowercases first. The one place text is split into words:
    the other callers differ only in these three settings."""
    if fold:
        text = text.lower()
    return [w for w in (_WORD_HYPHEN if hyphens else _WORD).findall(text) if len(w) >= min_len]


def fts_words(text: str) -> list[str]:
    """The words FTS5's ``unicode61`` tokenizer (the one its ``porter`` wraps) makes of
    ``text``: lower-cased, diacritics removed, split at every character that is not a letter
    or a digit (an underscore splits, so ``adopt_existing`` is ``adopt`` and ``existing``).
    What the FTS5-less fallback ranks on, so it sees the same words FTS5 indexes."""
    folded = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c)
    )
    return _FTS_WORD.findall(folded)


def tokens(title: str, body: str = "") -> list[str]:
    """The content words of a record, in order, repeats kept (they are its term counts)."""
    text = f"{title}. {body}"
    if "Imported from" in text:
        text = _IMPORTED.sub(" ", text)
    out: list[str] = []
    for run in _RUN.findall(text):
        out.extend(_run(run))
    if "--" in text:
        out.extend(_FLAG.findall(text))
    return out


def content_words(toks: Iterable[str]) -> int:
    """Distinct content words: how much a record says, for the ask rule's minimum."""
    return len({t for t in toks if not t.startswith("--")})


def digest(title: str, body: str = "") -> str:
    """Identity of a record's text up to case and whitespace: equal digests are the
    'identical text' D-no-duplicates records as a duplicate without asking."""
    norm = normalize_text(f"{title}\n{body}")
    return content_digest(norm, "blake2b", size=10)


def idf(df: int, n: int) -> float:
    """Smoothed inverse document frequency. A term no record has (df 0) weighs most."""
    return math.log((1 + n) / (1 + df)) + 1.0


def vector(toks: Iterable[str], df: Mapping[str, int], n: int) -> dict[str, float]:
    """Unit-length TF-IDF weights, sublinear in term count. Insertion order is first
    occurrence, so a vector built twice from the same text sums in the same order."""
    return _unit(toks, lambda t: idf(df.get(t, 0), n))


def _unit(toks: Iterable[str], weight: Callable[[str], float]) -> dict[str, float]:
    # `c == 1` spelled out because `invert` spells it so: 1.0 + log(1) is 1.0 exactly,
    # and the two must produce bit-identical weights for the store and memory to agree.
    v = {
        t: (weight(t) if c == 1 else (1.0 + math.log(c)) * weight(t))
        for t, c in Counter(toks).items()
    }
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


#: One term's postings: the ordinals of the records holding it, and its weight in each.
Postings = tuple[array, array]


def invert(corpus: Sequence[Sequence[str]]) -> tuple[dict[str, int], dict[str, Postings]]:
    """Document frequencies and the inverted index of a tokenized corpus.

    Ordinal ``i`` is ``corpus[i]``. A term's document frequency is the length of its
    postings, so storing the postings stores the IDF with them."""
    df: Counter[str] = Counter()
    for toks in corpus:
        df.update(set(toks))
    n = len(corpus)
    # The same arithmetic as `vector`, with each term's IDF computed once rather than
    # once per record holding it.
    idfs = {t: idf(c, n) for t, c in df.items()}
    log = math.log
    post: dict[str, Postings] = {}
    for i, toks in enumerate(corpus):
        v = {t: (idfs[t] if c == 1 else (1.0 + log(c)) * idfs[t]) for t, c in Counter(toks).items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        for t, x in v.items():
            p = post.get(t)
            if p is None:
                p = post[t] = (array("q"), array("d"))
            p[0].append(i)
            p[1].append(x / norm)
    return dict(df), post


def cosine(query: Mapping[str, float], postings: Mapping[str, Postings]) -> dict[int, float]:
    """Cosine of a unit query vector with every record sharing a term with it, by
    ordinal. Exact: a record left out shares no term, so its cosine is 0."""
    acc: dict[int, float] = {}
    get = acc.get
    for t, qw in query.items():
        p = postings.get(t)
        if p is None:
            continue
        for i, w in zip(p[0], p[1], strict=True):
            acc[i] = get(i, 0.0) + qw * w
    return acc
