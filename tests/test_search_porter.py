"""The fallback word ranker stems as FTS5's porter tokenizer does (B-uni-search-core.6-porter).

`infra.store` ranks words with FTS5 where the build has it and with `core.porter` where it
does not, so the two have to agree on every stem or a query returns different rows by machine
(`running` -> `runn` against FTS5's `run`). The agreement is checked three ways: a table of
stems written down (so a build WITHOUT FTS5 still holds the line), the running SQLite asked
for the stem of every word in a corpus (so a different SQLite cannot drift unseen), and the
rule FTS5 applies to a token it will not stem.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from ddflow.core import porter
from ddflow.infra import store

#: Word -> the stem FTS5's porter tokenizer gave it (SQLite 3.50.4), in the families the
#: algorithm's steps are about, and the edge cases the first port got wrong.
STEMS = {
    "running": "run", "runs": "run", "ponies": "poni", "pony": "poni", "caresses": "caress",
    "cats": "cat", "agreed": "agre", "feeds": "feed", "feeding": "feed", "feed": "feed",
    "plastered": "plaster", "motoring": "motor", "conflated": "conflat", "troubled": "troubl",
    "troubles": "troubl", "sized": "size", "hopping": "hop", "falling": "fall",
    "hissing": "hiss", "fizzed": "fizz", "failing": "fail", "filing": "file", "happy": "happi",
    "relational": "relat", "conditional": "condit", "rational": "ration", "valenci": "valenc",
    "hesitanci": "hesit", "digitizer": "digit", "conformabli": "conform", "radicalli": "radic",
    "differentli": "differ", "vileli": "vile", "analogousli": "analog",
    "vietnamization": "vietnam", "predication": "predic", "operator": "oper",
    "feudalism": "feudal", "decisiveness": "decis", "hopefulness": "hope",
    "callousness": "callous", "formaliti": "formal", "sensitiviti": "sensit",
    "sensibiliti": "sensibl", "triplicate": "triplic", "formative": "form",
    "formalize": "formal", "electriciti": "electr", "electrical": "electr", "hopeful": "hope",
    "goodness": "good", "revival": "reviv", "allowance": "allow", "inference": "infer",
    "airliner": "airlin", "gyroscopic": "gyroscop", "adjustable": "adjust",
    "defensible": "defens", "irritant": "irrit", "replacement": "replac",
    "adjustment": "adjust", "dependent": "depend", "adoption": "adopt", "homologou": "homolog",
    "communism": "commun", "activate": "activ", "angulariti": "angular", "effective": "effect",
    "bowdlerize": "bowdler", "probate": "probat", "cease": "ceas", "controll": "control",
    "generously": "gener", "national": "nation", "generalization": "gener",
    "oscillators": "oscil", "walking": "walk", "ays": "ai", "skis": "ski", "tied": "ti",
    # a suffix FTS5 only strips from a word LONGER than the suffix
    "eed": "e", "eeds": "e", "ies": "ie", "sies": "si", "sses": "sse", "asses": "ass",
    # a doubled y counts as a doubled consonant
    "sayyed": "sai", "ayyed": "ai", "yyed": "y",
    # too short to stem, and the word that is only a suffix
    "ab": "ab", "abc": "abc", "ed": "ed", "ing": "ing", "s": "s", "is": "is",
}  # fmt: skip


@pytest.mark.parametrize(("word", "want"), sorted(STEMS.items()))
def test_a_word_stems_as_fts5_stemmed_it(word: str, want: str) -> None:
    assert porter.stem(word) == want


def test_a_token_longer_than_64_bytes_is_left_alone() -> None:
    """FTS5 passes a long token through unstemmed; the bytes count, not the letters."""
    assert porter.stem("a" * 63 + "s") == "a" * 63
    assert porter.stem("a" * 64 + "s") == "a" * 64 + "s"
    wide = "é" * 33  # 66 bytes in 33 letters
    assert porter.stem(wide + "s") == wide + "s"


def test_the_fallback_ranker_uses_it() -> None:
    assert store._fallback_words("Running ponies, caresses") == ["run", "poni", "caress"]
    assert store._fallback_words("a") == ["a"]


def _fts5_stems(words: list[str]) -> dict[str, str] | None:
    """The running SQLite's own stem of each word, or None where it has no FTS5."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute("create virtual table t using fts5(x, tokenize='porter')")
    except sqlite3.OperationalError:
        return None
    con.executemany("insert into t(rowid, x) values (?, ?)", list(enumerate(words, 1)))
    con.execute("create virtual table v using fts5vocab(t, 'instance')")
    return {words[doc - 1]: term for doc, term in con.execute("select doc, term from v")}


def _corpus() -> list[str]:
    """Every lower-case word of up to 30 letters in the repository's own text, plus the
    inflections a stemmer is about: the same words with each of these endings."""
    root = Path(__file__).resolve().parents[1]
    words: set[str] = set()
    for path in [*root.glob("ddflow/**/*.py"), *root.glob("*.md"), *root.glob("docs/**/*.md")]:
        words.update(re.findall(r"[a-z]{1,30}", path.read_text("utf-8", errors="replace").lower()))
    base = sorted(words)
    endings = ("", "s", "es", "ed", "ing", "ly", "ness", "ful", "al", "ation", "ize", "ive",
               "able", "ment", "ent", "er", "ous", "ies", "sses", "eed", "ion", "e", "y")  # fmt: skip
    return sorted({(w + e)[:40] for w in base[::3] for e in endings} | set(base))


def test_every_word_of_the_repository_stems_as_the_running_sqlite_stems_it() -> None:
    words = _corpus()
    fts = _fts5_stems(words)
    if fts is None:
        pytest.skip("this SQLite has no FTS5: the table above is what holds the line here")
    assert len(words) > 5_000, "the corpus is the check; an empty one proves nothing"
    wrong = [(w, fts[w], porter.stem(w)) for w in words if w in fts and fts[w] != porter.stem(w)]
    assert not wrong, f"{len(wrong)} words stem differently from FTS5; first: {wrong[:10]}"
    assert len(fts) > len(words) * 0.9, "FTS5 should have indexed (nearly) every word"
