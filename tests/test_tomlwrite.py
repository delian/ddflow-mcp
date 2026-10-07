"""One TOML writer: `tomlcfg.upsert` (tomlkit) edits a config in place (B-uni-fsio-toml).

CASES is (id, text, `section.key`, value literal, text after the edit). Twelve of the
eighteen are byte-identical to what the hand-built line editor this replaced produced; the
other six are in DIFFERENT with what that editor wrote and why the new text is the one kept
(all valid TOML, all keep more of what the operator wrote). The module also pins that
every value the shared writer produces round-trips through tomllib.
"""

from __future__ import annotations

import tomllib

import pytest

from ddflow.infra import tomlcfg as TC

CASES = [
    (0, "", "a.b", "1", "[a]\nb = 1\n"),
    (1, "a = 1\n", "s.k", '"v"', 'a = 1\n\n[s]\nk = "v"\n'),
    (
        2,
        '[gates]  # how\nx = 1  # note\ny = [\n  "a",\n  "b",\n]\n\n# next\n[other]\nz = 2\n',
        "gates.x",
        "5",
        '[gates]  # how\nx = 5  # note\ny = [\n  "a",\n  "b",\n]\n\n# next\n[other]\nz = 2\n',
    ),
    (
        3,
        '[gates]  # how\nx = 1  # note\ny = [\n  "a",\n  "b",\n]\n\n# next\n[other]\nz = 2\n',
        "gates.y",
        '["q"]',
        '[gates]  # how\nx = 1  # note\ny = ["q"]\n\n# next\n[other]\nz = 2\n',
    ),
    (
        4,
        "[gates]\nx = 1\n\n[other]\nz = 2\n",
        "gates.new",
        '"v"',
        '[gates]\nx = 1\nnew = "v"\n[other]\nz = 2\n',
    ),
    (
        5,
        "[gate.lint]\ncommand = \"sed 's/\\\\[//g'\"\ntimeout_s = 900\n",
        "gate.lint.command",
        '"x"',
        '[gate.lint]\ncommand = "x"\ntimeout_s = 900\n',
    ),
    (
        6,
        '[gate.lint]\ncommand = "a"\n',
        "gate.test.command",
        '"b"',
        '[gate.lint]\ncommand = "a"\n\n[gate.test]\ncommand = "b"\n',
    ),
    (7, "[a]\nx = 1\n", "a.b.c", "1", "[a]\nx = 1\n\n[a.b]\nc = 1\n"),
    (8, "[a.b]\nx = 1\n", "a.b.y", "2", "[a.b]\nx = 1\ny = 2\n"),
    (9, "[a.b]\nx = 1\n", "a.z", "2", "[a]\nz = 2\n[a.b]\nx = 1\n"),
    (10, "[a]\nx = '''\nmulti\n[not]\nline\n'''\ny = 2\n", "a.x", '"s"', '[a]\nx = "s"\ny = 2\n'),
    (11, "# only a comment\n", "a.b", "true", "# only a comment\n\n[a]\nb = true\n"),
    (12, "[a]\nx = 1\n[b]\ny = 2\n", "a.x", "2", "[a]\nx = 2\n[b]\ny = 2\n"),
    (13, "[a]\nx=1\n", "a.x", "2", "[a]\nx=2\n"),
    (14, "[a]\nx = 1\n\n\n", "a.y", "2", "[a]\nx = 1\ny = 2\n"),
    (15, "[a]\n  x = 1\n", "a.x", "2", "[a]\n  x = 2\n"),
    (16, "top = 1\n[a]\nx = 1\n", "a.top", "2", "top = 1\n[a]\nx = 1\ntop = 2\n"),
    (17, "[a]\nx = 1\nx2 = 2\n", "a.x", "{ q = 1 }", "[a]\nx = { q = 1 }\nx2 = 2\n"),
]

#: id -> (what the old line editor wrote, why the text in CASES differs)
DIFFERENT = {
    2: (
        '[gates]  # how\nx = 5\ny = [\n  "a",\n  "b",\n]\n\n# next\n[other]\nz = 2\n',
        "a trailing comment on the replaced key is kept (the old editor dropped it)",
    ),
    4: (
        '[gates]\nx = 1\n\nnew = "v"\n[other]\nz = 2\n',
        "a new key lands right after the section's last key (the old editor put it after the blank "
        "line, against the next header)",
    ),
    9: (
        "[a.b]\nx = 1\n\n[a]\nz = 2\n",
        "a table that has to exist before its sub-table is written first (valid TOML either way)",
    ),
    13: ("[a]\nx = 2\n", "the spelling of the key (`x=1`) is kept, not normalised"),
    14: (
        "[a]\nx = 1\n\n\ny = 2\n",
        "blank lines at the end of a section are not kept in front of a new key",
    ),
    15: ("[a]\nx = 2\n", "the indentation of the key is kept"),
}


@pytest.mark.parametrize(("ident", "text", "dotted", "literal", "expected"), CASES)
def test_upsert_edits_in_place(ident, text, dotted, literal, expected) -> None:
    out = TC.upsert(text, dotted, literal)
    assert out == expected
    section, _, key = dotted.rpartition(".")
    node = tomllib.loads(out)
    for part in section.split("."):
        node = node[part]
    assert node[key] == tomllib.loads(f"v = {literal}")["v"]


def test_every_difference_from_the_old_editor_is_declared() -> None:
    assert set(DIFFERENT) <= {c[0] for c in CASES}
    for ident, (old, why) in DIFFERENT.items():
        assert old != CASES[ident][4] and why


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        ("text", '"text"'),
        ('say "hi"\n', '"say \\"hi\\"\\n"'),
        (True, "true"),
        (3, "3"),
        (["a", "b"], '["a", "b"]'),
        ({"k": "v"}, '{k = "v"}'),
    ],
)
def test_value_is_the_one_value_writer(value, literal) -> None:
    assert TC.value(value) == literal
    assert tomllib.loads(f"v = {TC.value(value)}")["v"] == value


@pytest.mark.parametrize(
    ("typed", "written"),
    [("true", "true"), ("12", "12"), ("1.5", "1.5"), ("[1, 2]", "[1, 2]"), ("plain", '"plain"'),
     ("a b", '"a b"'), ("--flag", '"--flag"')],
)  # fmt: skip
def test_literal_keeps_what_is_already_a_value_and_quotes_the_rest(typed, written) -> None:
    assert TC.literal(typed) == written


def test_tomlkit_is_used_in_one_module() -> None:
    """The adapter rule (D-unify 2): tests/test_architecture_guards.py counts it too."""
    import ddflow.infra.tomlcfg as home

    assert "tomlkit" in open(home.__file__, encoding="utf-8").read()
