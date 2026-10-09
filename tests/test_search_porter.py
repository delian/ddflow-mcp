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

from ddflow.core import porter, textsim
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


def test_the_query_terms_are_raw_words_the_fallback_then_stems_like_the_rows() -> None:
    """`_terms` stems nothing: both rankers get the raw words, and the fallback runs the QUERY
    through `_fallback_words` as it runs every row, so the two sides stem alike."""
    terms = store._terms("Running ponies")
    assert terms == ["Running", "ponies"]  # raw: case and all
    assert store._fallback_words(" ".join(terms)) == ["run", "poni"]
    assert (
        store._fallback_words("the daemon runs forever")[2] == store._fallback_words("running")[0]
    )


def test_the_fallback_splits_words_as_fts5_does() -> None:
    """Written down, so a build without FTS5 holds the line too: an underscore splits,
    diacritics go, case folds, digits stay."""
    assert store._fallback_words("adopt_existing") == ["adopt", "exist"]
    assert store._fallback_words("Café RÉSUMÉS x1y2 foo-bar") == [
        "cafe",
        "resum",
        "x1y2",
        "foo",
        "bar",
    ]
    assert textsim.fts_words("__init__ a.b/c") == ["init", "a", "b", "c"]


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


def _lines() -> list[str]:
    """Lines of the repository's own text, and the shapes a tokenizer tells apart: an
    underscore, accents, a ligature, digits, other scripts, superscripts."""
    root = Path(__file__).resolve().parents[1]
    lines: set[str] = set()
    for path in [
        root / "README.md",
        *sorted(root.glob("docs/**/*.md"))[:40],
        *sorted(root.glob("ddflow/core/*.py")),
    ]:
        lines.update(
            ln for ln in path.read_text("utf-8", errors="replace").splitlines() if ln.strip()
        )
    shapes = ["café résumé naïve", "foo_bar adopt_existing __init__", "x1y2 3d 2nd", "日本語 テスト",
              "ﬁne ﬂow", "a-b c.d e/f", "Ünïcödé ÅNGSTRÖM", "αβγ δ", "тест слово", "n°1 ½ ²",
              "ёлка Ёж", "αγορά Ελλάδα άλφα", "\u00b5s \u017ftill \u03c2\u03b1 \u0130stanbul", "한국어 문장", "ǟ ǖ ǻ", "ǆ Ǆ ǈ", "straße ŉ"]  # fmt: skip
    return sorted(lines)[:8000] + shapes


def test_every_line_splits_and_stems_into_the_terms_fts5_indexes() -> None:
    lines = _lines()
    con = sqlite3.connect(":memory:")
    try:
        con.execute("create virtual table t using fts5(x, tokenize='porter')")
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no FTS5: the written-down cases above hold the line here")
    con.executemany("insert into t(rowid, x) values (?, ?)", list(enumerate(lines, 1)))
    con.execute("create virtual table v using fts5vocab(t, 'instance')")
    indexed: dict[int, set[str]] = {}
    for doc, term in con.execute("select doc, term from v"):
        indexed.setdefault(doc, set()).add(term)
    assert len(lines) > 1_000
    wrong = [
        (line[:60], sorted(mine ^ indexed.get(i, set()))[:5])
        for i, line in enumerate(lines, 1)
        if (mine := set(store._fallback_words(line))) != indexed.get(i, set())
    ]
    assert not wrong, f"{len(wrong)} lines tokenize differently from FTS5; first: {wrong[:5]}"


def test_both_backends_return_the_same_rows_for_stem_family_queries(repo, log):
    """Through the store, query side and document side: a query is stemmed by the same
    function as the rows it ranks, so `running` finds `runs` and `conditions` find
    `conditional` on a build without FTS5 exactly as with it."""
    from ddflow.config import Config
    from ddflow.infra.store import _has_fts5

    if not _has_fts5():
        pytest.skip("this SQLite has no FTS5: only the fallback exists here")
    rows = {
        "L1": ("Daemon lifetimes", "the daemon runs forever"),
        "L2": ("Conditional approvals", "approve only if the conditions hold"),
        "L3": ("Ponies and caresses", "a pony caresses another"),
        "L4": ("Unrelated entry", "nothing here matches"),
    }
    for rid, (title, rule) in rows.items():
        log.append("lesson.recorded", rid, {"title": title, "rule": rule})
    found: dict[str, dict[str, list[str]]] = {}
    for backend in ("fts5", "like"):
        cfg = Config.load()
        cfg.lessons.search_backend = backend
        st = store.Store(repo, cfg)
        st.rebuild(log)
        found[backend] = {
            q: sorted(r["id"] for r in st.search("lessons", q, 10))
            for q in ("running", "runs", "condition", "conditionally", "pony", "caressing", "zzzqq")
        }
    assert found["fts5"] == found["like"], found
    assert found["like"]["running"] == ["L1"] and found["like"]["condition"] == ["L2"]
    assert found["like"]["caressing"] == ["L3"] and found["like"]["zzzqq"] == []


def test_every_short_word_over_the_letters_the_rules_look_at_stems_as_fts5_stems_it() -> None:
    """All strings of up to four letters over the vowels, y and the consonants the suffix
    rules mention (70,000 words): the corner cases of a rule table live in short words
    (``ated``, ``izing``, ``sses``) that a corpus of real text rarely holds."""
    import itertools

    alphabet = "aeiouybzdlstgnrx"
    words = ["".join(t) for n in range(1, 5) for t in itertools.product(alphabet, repeat=n)]
    fts = _fts5_stems(words)
    if fts is None:
        pytest.skip("this SQLite has no FTS5: the written-down stems above hold the line here")
    wrong = [(w, fts[w], porter.stem(w)) for w in words if w in fts and fts[w] != porter.stem(w)]
    assert len(words) > 60_000 and not wrong, wrong[:10]
