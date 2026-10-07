"""One text toolkit, slice 1: ONE splitter of text into words (B-uni-textkit.1-tokens).

Before this slice a query reached FTS5 through ``store._fts_query``, the LIKE fallback
through an inline ``re.split``, and ``Rule.similarity`` through ``rules._tokenize``: three
splitters with three spellings of one idea, next to ``textsim.tokens`` (the stemmed form for
weighing similarity). They are now settings of ``textsim.words``.

GOLDEN is what the three produced BEFORE the change, on text chosen to pull them apart
(case, hyphens, flags, unicode, one-letter words, more terms than a query may carry); it was
generated from the old code and is never regenerated from the new. The module pins that every
caller still yields it, byte for byte.
"""

from __future__ import annotations

import inspect
import re

import pytest

from ddflow.core import textsim
from ddflow.infra.store import MIN_TERM_CHARS, _fts_query, _like_terms
from ddflow.services.rules import _tokenize

#: (text, fts expression, LIKE terms, rule tokens), from the code as it was before the slice
GOLDEN = [
    ("", "", [], []),
    ("a", "", [], []),
    ("ab", '"ab"', ["ab"], []),
    ("abc", '"abc"', ["abc"], ["abc"]),
    ("Hello, World!", '"Hello" OR "World"', ["Hello", "World"], ["hello", "world"]),
    (
        "foo-bar baz_qux",
        '"foo" OR "bar" OR "baz_qux"',
        ["foo", "bar", "baz_qux"],
        ["foo-bar", "baz_qux"],
    ),
    (
        "--no-worktree flag",
        '"no" OR "worktree" OR "flag"',
        ["no", "worktree", "flag"],
        ["--no-worktree", "flag"],
    ),
    (
        "adopt_existing Store.rebuild ddflow/infra/store.py",
        '"adopt_existing" OR "Store" OR "rebuild" OR "ddflow" OR "infra" OR "store" OR "py"',
        ["adopt_existing", "Store", "rebuild", "ddflow", "infra", "store", "py"],
        ["adopt_existing", "store", "rebuild", "ddflow", "infra", "store"],
    ),
    (
        "inferWorktree HTTPServer",
        '"inferWorktree" OR "HTTPServer"',
        ["inferWorktree", "HTTPServer"],
        ["inferworktree", "httpserver"],
    ),
    (
        'unbalanced "quote and - dash',
        '"unbalanced" OR "quote" OR "and" OR "dash"',
        ["unbalanced", "quote", "and", "dash"],
        ["unbalanced", "quote", "and", "dash"],
    ),
    (
        "tabs\tand\nnewlines  and   spaces",
        '"tabs" OR "and" OR "newlines" OR "and" OR "spaces"',
        ["tabs", "and", "newlines", "and", "spaces"],
        ["tabs", "and", "newlines", "and", "spaces"],
    ),
    (
        "Ünïcode café naïve 日本語 text",
        '"Ünïcode" OR "café" OR "naïve" OR "日本語" OR "text"',
        ["Ünïcode", "café", "naïve", "日本語", "text"],
        ["ünïcode", "café", "naïve", "日本語", "text"],
    ),
    (
        "x-ray a-b -- - --- trailing-",
        '"ray" OR "trailing"',
        ["ray", "trailing"],
        ["x-ray", "a-b", "---", "trailing-"],
    ),
    (
        "ID: B5c32cbb5c1, sha 0123abc",
        '"ID" OR "B5c32cbb5c1" OR "sha" OR "0123abc"',
        ["ID", "B5c32cbb5c1", "sha", "0123abc"],
        ["b5c32cbb5c1", "sha", "0123abc"],
    ),
    (
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
        '"one" OR "two" OR "three" OR "four" OR "five" OR "six" OR "seven" OR "eight" OR "nine" OR "ten" '
        'OR "eleven" OR "twelve"',
        ["one", "two", "three", "four", "five", "six", "seven", "eight"],
        [
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
        ],
    ),
    (
        "NOT AND OR NEAR(a b) col:val ^start end*",
        '"NOT" OR "AND" OR "OR" OR "NEAR" OR "col" OR "val" OR "start" OR "end"',
        ["NOT", "AND", "OR", "NEAR", "col", "val", "start", "end"],
        ["not", "and", "near", "col", "val", "start", "end"],
    ),
    (
        "snake_case_name kebab-case-name dot.name",
        '"snake_case_name" OR "kebab" OR "case" OR "name" OR "dot" OR "name"',
        ["snake_case_name", "kebab", "case", "name", "dot", "name"],
        ["snake_case_name", "kebab-case-name", "dot", "name"],
    ),
    ("1234 12 123", '"1234" OR "12" OR "123"', ["1234", "12", "123"], ["1234", "123"]),
    ("a.b.c d_e f-g", '"d_e"', ["d_e"], ["d_e", "f-g"]),
]


@pytest.mark.parametrize(("text", "fts", "like", "rules"), GOLDEN, ids=[g[0][:20] for g in GOLDEN])
def test_every_caller_yields_what_it_did_before(text, fts, like, rules) -> None:
    assert _fts_query(text) == fts
    assert _like_terms(text) == like
    assert _tokenize(text) == rules


def test_fts_and_like_are_the_same_words_with_different_caps() -> None:
    text = " ".join(f"word{i:02d}" for i in range(20))
    assert _like_terms(text) == [f"word{i:02d}" for i in range(8)]
    assert _fts_query(text).count(" OR ") == 11


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        ("Foo-Bar baz", {}, ["Foo", "Bar", "baz"]),
        ("Foo-Bar baz", {"hyphens": True}, ["Foo-Bar", "baz"]),
        ("Foo-Bar baz", {"hyphens": True, "fold": True}, ["foo-bar", "baz"]),
        ("a bb ccc", {}, ["bb", "ccc"]),
        ("a bb ccc", {"min_len": 3}, ["ccc"]),
        ("a bb ccc", {"min_len": 1}, ["a", "bb", "ccc"]),
        ("", {}, []),
        ("!!! ???", {}, []),
    ],
)
def test_words_settings(text, kwargs, expected) -> None:
    assert textsim.words(text, **kwargs) == expected


def test_words_is_not_tokens() -> None:
    """`tokens` stems and drops stop words for similarity; `words` is literal."""
    assert textsim.words("The claimed worktrees") == ["The", "claimed", "worktrees"]
    assert textsim.tokens("The claimed worktrees") != textsim.words("The claimed worktrees")


def test_no_second_word_splitter_is_left_behind() -> None:
    """The callers that used to spell their own splitter now call `textsim.words`: no
    regular expression over word characters is left in them, whichever way it is spelled."""
    spelling = re.compile(
        r"re\.(split|findall|sub|finditer|compile)\(\s*r?[\"'][^\"']*(\\[wW]|\[\^?\\w)"
    )
    for path in ("ddflow/infra/store.py", "ddflow/services/rules.py"):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        hits = [n for n, line in enumerate(lines, 1) if spelling.search(line)]
        assert not hits, (path, hits)
    assert MIN_TERM_CHARS is textsim.MIN_WORD_CHARS
    # and they delegate: the functions themselves name `textsim.words`
    for fn in (_fts_query, _like_terms, _tokenize):
        assert "textsim.words(" in inspect.getsource(fn), fn.__name__
