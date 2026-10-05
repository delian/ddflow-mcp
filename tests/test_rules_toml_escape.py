"""Bug B28cab0652a: Rule.to_toml wrote string values with no escaping.

A title such as `say "hi"` produced front matter tomllib refuses, so the rule could
never be read back. Every string field must survive a to_toml -> from_toml round trip.
"""

from __future__ import annotations

import tomllib

import pytest

from ddflow.services.rules import Rule

AWKWARD = [
    'say "hi"',
    "back\\slash",
    "C:\\path\\to\\file",
    "line one\nline two",
    "blank\n\nline",
    "tab\there",
    "café ünïcode",
    "ship it 🚀",
    "del\x7fchar",
    "nul\x00and\x1fctl",
    "true",
    "[not, a, list]",
    "'single' quotes",
    '"""triple"""',
    "trailing backslash \\",
]


@pytest.mark.parametrize("text", AWKWARD)
def test_rule_string_fields_round_trip(text: str) -> None:
    rule = Rule(
        id="r-escape",
        title=text,
        content="Body text.",
        tags=[text, "plain"],
        scope=text,
        globs=[text, "**/*.py"],
    )
    back = Rule.from_toml(rule.to_toml())
    assert back.title == text
    assert back.tags == [text, "plain"]
    assert back.scope == text
    assert back.globs == [text, "**/*.py"]
    assert back.content == "Body text."


def test_front_matter_is_valid_toml_for_a_quoted_title() -> None:
    """The exact probe from the bug: a double quote in the title."""
    rule = Rule(id="r-quote", title='say "hi"', content="x")
    front = rule.to_toml().split("\n\n", 1)[0]
    assert tomllib.loads(front)["title"] == 'say "hi"'
