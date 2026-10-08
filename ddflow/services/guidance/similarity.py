"""How alike two pieces of guidance read: one tokenizer, one score, one candidate list.

`Rule` carried its own tokenizer and Jaccard score, and `api.rules` its own candidate
loop. The tokenizer is `core.textsim.words`; the score and the loop are here, for any kind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...core import textsim
from .record import GuidanceRecord

#: Words this short are noise: ``textsim.words`` keeps a word of at least this many characters.
_MIN_WORD = 3
#: The score at which two pieces of guidance read as the same thing.
DEFAULT_THRESHOLD = 0.55
MAX_OVERLAP = 5


def words(text: str) -> list[str]:
    """Lowercase words of three or more characters, hyphens kept inside a word."""
    return textsim.words(text, min_len=_MIN_WORD, hyphens=True, fold=True)


def jaccard(a: str, b: str) -> float:
    """0.0 (nothing shared) to 1.0 (the same words) between two texts; two empty texts, or two
    texts with no word, are the same only if they are equal."""
    if not a or not b:
        return 1.0 if a == b else 0.0
    mine, theirs = set(words(a)), set(words(b))
    if not mine or not theirs:
        return 1.0 if a == b else 0.0
    return len(mine & theirs) / len(mine | theirs)


def overlap_terms(a: str, b: str, limit: int = MAX_OVERLAP) -> list[str]:
    """The words two texts share, alphabetically, at most ``limit``."""
    return sorted(set(words(a)) & set(words(b)))[:limit]


def compared(rec: GuidanceRecord, title: str, content: str) -> tuple[float, str, str]:
    """(score, the new text, ``rec``'s text) for new guidance against ``rec``.

    Content against content, as always, while both have content. Guidance with no content
    is title-only, and content alone scored every pair of them 1.0 (two empty contents are
    "identical"): a second title-only rule was refused as a duplicate of each of them
    whatever it said (bug Bd4c9bcb87e). So two title-only ones compare their titles, and a
    title-only one against one with content compares title and content together. With no
    ``title`` given, the content-only comparison is kept."""
    if not title or (content and rec.body):
        return jaccard(rec.body, content), content, rec.body
    if not content and not rec.body:
        mine, theirs = title, rec.title
    else:
        mine = "\n".join(x for x in (title, content) if x)
        theirs = "\n".join(x for x in (rec.title, rec.body) if x)
    return jaccard(theirs, mine), mine, theirs


@dataclass(frozen=True)
class Similar:
    record: GuidanceRecord
    score: float
    overlap: list[str]


def similar(
    records: Iterable[GuidanceRecord],
    content: str,
    *,
    title: str = "",
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Similar]:
    """The ``records`` that read like new guidance with this ``content`` (and ``title``), best
    first; equal scores keep the order given."""
    found: list[Similar] = []
    for rec in records:
        score, mine, theirs = compared(rec, title, content)
        if score >= threshold:
            found.append(Similar(rec, score, overlap_terms(mine, theirs)))
    found.sort(key=lambda s: -s.score)
    return found
