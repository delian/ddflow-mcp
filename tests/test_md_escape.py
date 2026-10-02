"""B3da43a71bb: md_escape escaped every underscore (run\\_nemo\\_run in generated documents).
It escapes what changes rendering, and leaves identifiers and ordinary punctuation alone."""

from __future__ import annotations

import pytest

from ddflow.services.export.registry import md_escape


@pytest.mark.parametrize(
    "text",
    [
        "run_nemo_run",
        "B-export-core",
        "snake_case_name",
        "[d]",
        "5 < 6",
        "a * b",
        "~/path",
        "1.5 ok",
    ],
)
def test_text_that_renders_as_itself_is_left_alone(text):
    assert md_escape(text) == text


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("a *b* _c_", "a \\*b\\* \\_c\\_"),
        ("__init__", "\\_\\_init\\_\\_"),
        ("trailing_", "trailing\\_"),
        ("x | y", "x \\| y"),
        ("back\\slash", "back\\\\slash"),
        ("`code`", "\\`code\\`"),
        ("<b>hi</b>", "\\<b>hi\\</b>"),
        ("# head", "\\# head"),
        ("> quote", "\\> quote"),
        ("- item", "\\- item"),
        ("* item", "\\* item"),
        ("1. one", "1\\. one"),
        ("[a](http://x)", "[a\\](http://x)"),
        ("~~gone~~", "\\~\\~gone\\~\\~"),
        ("fish &amp; chips", "fish \\&amp; chips"),
    ],
)
def test_what_changes_rendering_is_escaped(text, want):
    assert md_escape(text) == want


def test_text_is_folded_onto_one_line():
    assert md_escape("a\n\n  b") == "a b"
