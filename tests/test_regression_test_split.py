"""Bug Bfc9daca269: a `]` inside a parametrize id must not stop a regression-test list
from splitting. The bracket depth went negative, so every later `,`/`;` was read as part
of one entry and a missing second test was never resolved."""

from __future__ import annotations

import subprocess

import pytest

from ddflow.api.knowledge.regression import _split_outside_brackets, _unresolved_tests


def test_a_closing_bracket_inside_an_id_does_not_swallow_the_next_entry():
    assert _split_outside_brackets("tests/a.py::t[x]y],tests/missing.py::t2") == [
        "tests/a.py::t[x]y]",
        "tests/missing.py::t2",
    ]
    assert _split_outside_brackets("tests/a.py::t[x]y];tests/b.py::t2") == [
        "tests/a.py::t[x]y]",
        "tests/b.py::t2",
    ]


def test_a_value_holding_brackets_and_a_comma_stays_one_entry():
    """Review of Bfc9daca269: clamping the depth at zero split this real node id (a
    parametrize value `a]b]c,d`) inside its own brackets."""
    assert _split_outside_brackets("tests/a.py::test[a]b]c,d]") == ["tests/a.py::test[a]b]c,d]"]
    assert _split_outside_brackets("tests/a.py::test[a]b]c,d],tests/b.py::u") == [
        "tests/a.py::test[a]b]c,d]",
        "tests/b.py::u",
    ]
    assert _split_outside_brackets("tests/a.py::t[[1]] , tests/b.py::u") == [
        "tests/a.py::t[[1]]",
        "tests/b.py::u",
    ]


@pytest.mark.parametrize(
    "node", ["tests/a.py::t[a],b]", "tests/a.py::t[[a],b]", "tests/a.py::t[[a] b,c]"]
)
def test_a_value_whose_inner_bracket_meets_a_separator_stays_one_entry(node):
    """Second review of Bfc9daca269: a `]` followed by a separator is not necessarily the
    end of the id."""
    assert _split_outside_brackets(node) == [node]
    assert _split_outside_brackets(f"{node};tests/b.py::u") == [node, "tests/b.py::u"]


@pytest.mark.parametrize(
    "spec,entries",
    [
        # a value holding a path-like token after a comma stays inside its brackets
        ("tests/a.py::t[a],b.py]", ["tests/a.py::t[a],b.py]"]),
        ("tests/a.py::t[a.py,b.py]", ["tests/a.py::t[a.py,b.py]"]),
        ("tests/a.py::t[a,tests/b.py]", ["tests/a.py::t[a,tests/b.py]"]),
        # a command after a bracketed id is still its own entry
        ("tests/a.py::t[x], pytest tests/b.py", ["tests/a.py::t[x]", "pytest tests/b.py"]),
        ("tests/a.py::t[1], pytest -k x", ["tests/a.py::t[1]", "pytest -k x"]),
        # two bracketed ids, the second one's value holding a bracket of its own
        ("tests/a.py::t[a],b],tests/c.py::d[e]", ["tests/a.py::t[a],b]", "tests/c.py::d[e]"]),
    ],
)
def test_third_review_cases(spec, entries):
    """Third review of Bfc9daca269: a value holding a `.py` token, and a command after a
    bracketed id."""
    assert _split_outside_brackets(spec) == entries


def test_entries_without_brackets_split_as_before():
    assert _split_outside_brackets("tests/a.py::t, pytest -k x ;tests/b.py") == [
        "tests/a.py::t",
        "pytest -k x",
        "tests/b.py",
    ]


def test_separators_inside_brackets_still_do_not_split():
    assert _split_outside_brackets("tests/a.py::t[1,2;3],tests/b.py::u") == [
        "tests/a.py::t[1,2;3]",
        "tests/b.py::u",
    ]


def test_the_missing_second_test_is_reported(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_param(x):\n    pass\n")
    missing, _ = _unresolved_tests(
        tmp_path, "tests/test_a.py::test_param[x]y],tests/test_missing.py::test_nope"
    )
    assert missing == ["tests/test_missing.py::test_nope"], missing
