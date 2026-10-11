"""The pinned-transcript normaliser ignores argparse's version-dependent formatting and
nothing else (B-pin-argparse-format)."""

from __future__ import annotations

from tests.pin_norm import argparse_stable, stable

WRAPPED_NEW = (
    "usage: ddflow link [-h]\n"
    "                   (--extends ID | --duplicate-of ID | --related ID | --distinct ID)\n"
    "                   [--reason REASON]\n"
    "                   subject\n"
    "ddflow link: error: one of the arguments --extends is required\n"
)
WRAPPED_OLD = (
    "usage: ddflow link [-h] (--extends ID | --duplicate-of ID | --related ID |\n"
    "                   --distinct ID) [--reason REASON]\n"
    "                   subject\n"
    "ddflow link: error: one of the arguments --extends is required\n"
)


def test_a_usage_line_wrapped_two_ways_reads_the_same():
    assert argparse_stable(WRAPPED_NEW) == argparse_stable(WRAPPED_OLD)
    assert argparse_stable(WRAPPED_NEW).splitlines()[1].startswith("ddflow link: error:")


def test_a_trailing_ellipsis_on_its_own_line_or_not_reads_the_same():
    a = "usage: ddflow [-h] {init,phase}\n              ...\nddflow: error: x\n"
    b = "usage: ddflow [-h] {init,phase} ...\nddflow: error: x\n"
    assert argparse_stable(a) == argparse_stable(b)


def test_choice_quoting_is_ignored_but_not_the_choices():
    old = "ddflow x: error: argument y: invalid choice: 'z' (choose from 'task', 'phase')\n"
    new = "ddflow x: error: argument y: invalid choice: 'z' (choose from task, phase)\n"
    assert argparse_stable(old) == argparse_stable(new)
    assert argparse_stable(old) != argparse_stable(new.replace("phase", "phasez"))
    assert argparse_stable(old) != argparse_stable(
        new.replace("choose from task", "choose from tasks")
    )
    # the offending VALUE keeps its quotes: that is ddflow's wording, not argparse's layout
    assert argparse_stable(old) != argparse_stable(old.replace("'z'", "z"))


def test_a_changed_usage_word_still_differs():
    changed = WRAPPED_OLD.replace("--distinct", "--different")
    assert argparse_stable(changed) != argparse_stable(WRAPPED_NEW)
    assert argparse_stable(WRAPPED_OLD.replace("[--reason REASON]", "[--why REASON]")) != (
        argparse_stable(WRAPPED_NEW)
    )
    assert argparse_stable(WRAPPED_OLD.replace("usage:", "Usage:")) != argparse_stable(WRAPPED_NEW)


def test_nothing_outside_a_usage_block_is_collapsed():
    a = "positional arguments:\n  x    help text\n\noptions:\n  -h   show\n"
    assert argparse_stable(a) == a
    assert argparse_stable(a) != argparse_stable(a.replace("  x    help", "  x help"))
    err = "ddflow link: error: two  spaces\n"
    assert argparse_stable(err) == err


def test_a_changed_error_line_after_the_usage_still_differs():
    assert argparse_stable(WRAPPED_NEW) != argparse_stable(
        WRAPPED_NEW.replace("required", "needed")
    )


def test_stable_walks_rows_and_tables():
    row_a = [["link"], 2, "", WRAPPED_NEW]
    row_b = [["link"], 2, "", WRAPPED_OLD]
    assert stable(row_a) == stable(row_b)
    assert stable({"k": row_a}) == stable({"k": row_b})
    assert stable((1, "x")) == (1, "x")


def test_quotes_are_stripped_only_from_a_list_of_quoted_items():
    assert argparse_stable("(choose from 'a', 'b')") == "(choose from a, b)"
    # not argparse's repr layout: left alone, so a change in what the quoting says shows
    assert argparse_stable("(choose from 'a, b)") == "(choose from 'a, b)"
    # an item that holds the separator would read as two choices once unquoted: untouched
    assert argparse_stable("(choose from 'a, b')") == "(choose from 'a, b')"
    assert argparse_stable("(choose from 'a, b', 'c')") == "(choose from 'a, b', 'c')"
    assert argparse_stable("(choose from 'a, b', 'c')") != argparse_stable(
        "(choose from 'a', 'b', 'c')"
    )
    assert argparse_stable("(choose from a, 'b')") == "(choose from a, 'b')"
    assert argparse_stable("(choose from 'a' 'b')") == "(choose from 'a' 'b')"


def test_whitespace_after_a_usage_block_still_matters():
    base = "usage: ddflow link [-h]\n                   subject\nddflow link: error: one of the arguments --extends is required\n"
    assert argparse_stable(base) != argparse_stable(base.replace("error: one", "error:  one"))
    assert argparse_stable(base) != argparse_stable(base.replace("\nddflow link", "\n ddflow link"))
    assert argparse_stable(base + "\n\n  indented  help\n").endswith("\n\n  indented  help\n")
