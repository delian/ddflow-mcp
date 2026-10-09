"""The Porter stemmer, as SQLite's FTS5 ``porter`` tokenizer applies it.

``infra.store`` ranks words with FTS5 where the build has it, and in Python where it does not
(a Python without FTS5, or a query FTS5 found nothing for). The two must stem alike, or the
same query returns different rows on different machines: the light suffix stemmer in
``textsim`` (``running`` -> ``runn``) and FTS5's (``running`` -> ``run``) disagreed beyond
plurals and ``-ed``/``-ing``. This is M. F. Porter's 1980 algorithm, checked against FTS5
itself over every word of the repository's own text (tests/test_search_porter.py asks the
SQLite that is running for each stem, so a different SQLite cannot drift unseen).

Pure, stdlib, deterministic. Input is a lower-cased word.
"""

from __future__ import annotations

import functools

_VOWELS = frozenset("aeiou")
#: FTS5 passes a token through unstemmed when it is shorter than 3 or longer than 64 bytes.
_MIN_STEMMED, _MAX_STEMMED_BYTES = 2, 64
_CVC_LEN = 3  # consonant, vowel, consonant


def _is_consonant(word: str, i: int) -> bool:
    c = word[i]
    if c in _VOWELS:
        return False
    if c == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """Porter's m: the number of vowel-then-consonant sequences in ``stem``."""
    n, i, m = len(stem), 0, 0
    while i < n and _is_consonant(stem, i):  # leading consonants
        i += 1
    while i < n:
        while i < n and not _is_consonant(stem, i):  # a vowel run
            i += 1
        if i >= n:
            break
        m += 1
        while i < n and _is_consonant(stem, i):  # then a consonant run
            i += 1
    return m


def _has_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _double_consonant(word: str) -> bool:
    """The last two letters are the same consonant. FTS5 judges the letter alone, a ``y`` being
    a consonant here whatever stands before it (``sayyed`` -> ``sai``)."""
    return len(word) > 1 and word[-1] == word[-2] and word[-1] not in _VOWELS


def _cvc(word: str) -> bool:
    """consonant-vowel-consonant ending, the last consonant not w, x or y."""
    n = len(word)
    return (
        n >= _CVC_LEN
        and word[-1].isascii()  # FTS5 knows only a-z here: ``feß`` is not consonant-vowel-consonant
        and _is_consonant(word, n - 1)
        and not _is_consonant(word, n - 2)
        and _is_consonant(word, n - 3)
        and word[-1] not in "wxy"
    )


def _replace(word: str, suffix: str, new: str, min_measure: int) -> tuple[str, bool]:
    """``(word with suffix -> new, True)`` when ``word`` ends in ``suffix`` (matched whether or
    not the measure allows the change); ``(word, False)`` when it does not end in it."""
    if not word.endswith(suffix):
        return word, False
    stem = word[: len(word) - len(suffix)]
    return (stem + new if _measure(stem) > min_measure else word), True


def _step1a(w: str) -> str:
    # FTS5 applies ``sses`` and ``ies`` only to a word longer than the suffix: ``ies`` is
    # ``ie`` and ``sses`` is ``sse`` (the plain ``s`` rule), not ``i`` and ``ss``.
    if w.endswith("sses") and len(w) > 4:  # noqa: PLR2004
        return w[:-2]
    if w.endswith("ies") and len(w) > 3:  # noqa: PLR2004
        return w[:-2]
    if w.endswith("ss"):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


def _step1b(w: str) -> str:
    if w.endswith("eed") and len(w) > 3:  # noqa: PLR2004 -- a bare ``eed`` is ``e`` + ``ed``
        stem = w[:-3]
        return w[:-1] if _measure(stem) > 0 else w
    for suffix in ("ed", "ing"):
        if w.endswith(suffix) and _has_vowel(w[: -len(suffix)]):
            w = w[: -len(suffix)]
            if w.endswith(("at", "bl", "iz")):
                return w + "e"
            if _double_consonant(w) and w[-1] not in "lsz":
                return w[:-1]
            if _measure(w) == 1 and _cvc(w):
                return w + "e"
            return w
    return w


def _step1c(w: str) -> str:
    if w.endswith("y") and _has_vowel(w[:-1]):
        return w[:-1] + "i"
    return w


_STEP2 = (
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
    ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"), ("eli", "e"),
    ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"), ("ator", "ate"),
    ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"),
    ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"), ("logi", "log"),
)  # fmt: skip
_STEP3 = (
    ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"),
    ("ful", ""), ("ness", ""),
)  # fmt: skip
_STEP4 = (
    "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion",
    "ou", "ism", "ate", "iti", "ous", "ive", "ize",
)  # fmt: skip


def _by_table(w: str, table: tuple[tuple[str, str], ...]) -> str:
    # the longest suffix that matches decides, whether or not its measure allows the change
    for suffix, new in sorted(table, key=lambda p: -len(p[0])):
        if w.endswith(suffix):
            return _replace(w, suffix, new, 0)[0]
    return w


def _step4(w: str) -> str:
    for suffix in sorted(_STEP4, key=lambda s: -len(s)):
        if w.endswith(suffix):
            stem = w[: len(w) - len(suffix)]
            if suffix == "ion" and not stem.endswith(("s", "t")):
                return w
            return stem if _measure(stem) > 1 else w
    return w


def _step5(w: str) -> str:
    if w.endswith("e"):
        stem = w[:-1]
        m = _measure(stem)
        if m > 1 or (m == 1 and not _cvc(stem)):
            w = stem
    if w.endswith("ll") and _measure(w) > 1:
        w = w[:-1]
    return w


@functools.lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """The stem of a lower-cased ``word`` (Porter 1980). A word of one or two bytes, or of more
    than 64, is left as it is, as FTS5's tokenizer leaves it."""
    size = len(word.encode("utf-8"))  # FTS5 counts bytes, not letters
    if size <= _MIN_STEMMED or size > _MAX_STEMMED_BYTES:
        return word
    w = _step1a(word)
    w = _step1b(w)
    w = _step1c(w)
    w = _by_table(w, _STEP2)
    w = _by_table(w, _STEP3)
    w = _step4(w)
    return _step5(w)
