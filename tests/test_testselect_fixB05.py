"""B05f890942b: a test asserts on a fragment of a message, not the whole of it, and without the
punctuation the source puts after it; the phrase route must still find that test."""

from __future__ import annotations

from ddflow.services import testidents as TI

SOURCE = ['    return "The branch has no commit ahead of main, so there is nothing to merge."']


def test_a_three_word_fragment_a_test_quotes_selects_the_test():
    phrases = dict.fromkeys(TI._phrases(SOURCE), "ddflow/api/gates.py")
    tests = {"tests/test_a.py": 'assert "no commit ahead" in out', "tests/test_b.py": "x = 1"}
    assert set(TI.phrase_tests(phrases, tests)) == {"tests/test_a.py"}


def test_a_quoted_phrase_drops_the_comma_the_source_puts_after_it():
    phrases = dict.fromkeys(
        TI._phrases(['"was claimed without a worktree, so"']), "ddflow/api/gates.py"
    )
    tests = {"tests/test_a.py": 'assert "claimed without a worktree" not in text'}
    assert set(TI.phrase_tests(phrases, tests)) == {"tests/test_a.py"}


def test_three_short_words_are_not_a_phrase():
    assert not any(len(p) < 15 for p in TI._phrases(['"it is a of the"']))


def test_the_stripped_form_keeps_the_floor_too():
    assert not any(len(p) < 15 for p in TI._phrases(['"value is wrong"', "'abc def ghi jk'"]))
