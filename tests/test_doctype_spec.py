"""Document types as data: parse, round trip, extends, layers, patterns, refusals."""

from __future__ import annotations

import pytest

from ddflow.services.doctypes import spec as S

FULL = """\
[doctype]
name = "policy"
title = "Policy"
path = "docs/POLICY.md"
genre = "policy"
owner = "@ops"
skeleton = "policy.md.j2"
triggers = ["ddflow/policy/"]
schema_version = 1
contract = "generated"

[doctype.review]
review_requires = "human"
full_review_every_days = 90
stamp = true

[doctype.validate]
rules = [{ kind = "max_bytes", limit = 20000 }]

[[doctype.section]]
id = "scope"
heading = "Scope"
level = 2
order = 1
required = true
ownership = "shared"
source = "config"
prompt = "Who does this bind?"
guidance = "One paragraph."
rules = ["no-todo"]

[[doctype.section]]
id = "rules"
heading = "R.*"
match = "regex"
order = 2
depends_on = ["scope"]
"""

ADR = """\
[doctype]
name = "adr"
title = "ADR"
pattern = "docs/adr/{seq:04}-{slug}.md"
[[doctype.section]]
id = "a"
heading = "A"
order = 1
[[doctype.section]]
id = "b"
heading = "B"
order = 2
"""


def test_every_field_round_trips():
    dt = S.parse(FULL, source="p.toml")
    again = S.build(dt.to_mapping(), S._Src(dt.source, ""))
    assert again == dt
    assert dt.sections[1].match == "regex" and dt.sections[0].rules == ("no-todo",)
    assert dt.review["full_review_every_days"] == 90 and dt.contract == "generated"


def test_collection_pattern_is_deterministic():
    dt = S.parse(ADR)
    assert dt.multi
    one = dt.expand(seq=7, slug="use-toml", date="2026-10-18")
    assert one == "docs/adr/0007-use-toml.md" == dt.expand(seq=7, slug="use-toml")
    assert S.parse(FULL).expand() == "docs/POLICY.md"


def test_unknown_key_is_refused_with_file_and_line():
    with pytest.raises(S.DocTypeError, match=r"x\.toml:6: unknown key 'bogus'"):
        S.parse(FULL.replace('genre = "policy"', 'genre = "policy"\nbogus = 1'), source="x.toml")
    with pytest.raises(S.DocTypeError, match=r"unknown key 'colour' in section 'a'"):
        S.parse(ADR.replace('heading = "A"', 'heading = "A"\ncolour = "red"'), source="x.toml")


def test_duplicate_section_id_is_refused():
    with pytest.raises(S.DocTypeError, match=r"x\.toml.*duplicate section id 'a'"):
        S.parse(ADR.replace('id = "b"', 'id = "a"'), source="x.toml")


@pytest.mark.parametrize(
    "bad,msg",
    [
        ('genre = "poem"', "genre 'poem'"),
        ('contract = "forever"', "contract 'forever'"),
    ],
)
def test_bad_values_name_the_file(bad, msg):
    with pytest.raises(S.DocTypeError, match=msg) as e:
        S.parse(ADR.replace('title = "ADR"', f'title = "ADR"\n{bad}'), source="x.toml")
    assert "x.toml" in str(e.value)


def test_unknown_placeholder_is_refused():
    with pytest.raises(S.DocTypeError, match=r"x\.toml.*placeholder"):
        S.parse(ADR.replace("{slug}", "{nope}"), source="x.toml")


def test_not_toml():
    with pytest.raises(S.DocTypeError, match=r"y\.toml: not TOML"):
        S.parse("[doctype", source="y.toml")


def test_rule_kind_added_as_data():
    text = ADR.replace(
        "[[doctype.section]]",
        '[doctype.validate]\nrules = [{ kind = "each_item_matches" }]\n[[doctype.section]]',
        1,
    )
    with pytest.raises(S.DocTypeError, match="each_item_matches"):
        S.parse(text)
    S.RULE_KINDS.register(S.RuleKind("each_item_matches"))
    try:
        assert S.parse(text).validate["rules"][0]["kind"] == "each_item_matches"
    finally:
        S.RULE_KINDS.unregister("each_item_matches")


def test_extends_merges_sections_by_id_and_removes():
    child = """\
[doctype]
name = "adr2"
extends = "adr"
title = "ADR v2"
[[doctype.section]]
id = "a"
heading = "Alpha"
[[doctype.section]]
id = "b"
remove = true
[[doctype.section]]
id = "c"
heading = "C"
order = 3
"""
    types = S.resolve([("shipped", [("adr.toml", ADR)]), ("project", [("adr2.toml", child)])])
    dt = types["adr2"]
    assert [s.id for s in dt.sections] == ["a", "c"] and dt.sections[0].heading == "Alpha"
    assert dt.title == "ADR v2" and dt.pattern == types["adr"].pattern and dt.multi


def test_layers_replace_or_extend_the_same_name():
    over = 'title = "Mine"\n'
    replace = ADR.replace('title = "ADR"', 'title = "Mine"')
    ext = f'[doctype]\nname = "adr"\nextends = "adr"\n{over}'
    types = S.resolve([("shipped", [("adr.toml", ADR)]), ("project", [("adr.toml", replace)])])
    assert types["adr"].title == "Mine"
    types = S.resolve([("shipped", [("adr.toml", ADR)]), ("config", [("adr.toml", ext)])])
    assert types["adr"].title == "Mine" and len(types["adr"].sections) == 2


def test_extends_errors_name_the_file():
    cyc = ADR.replace('name = "adr"', 'name = "p"\nextends = "q"')
    cyc2 = ADR.replace('name = "adr"', 'name = "q"\nextends = "p"')
    with pytest.raises(S.DocTypeError, match="cycle"):
        S.resolve([("l", [("p.toml", cyc), ("q.toml", cyc2)])])
    lone = ADR.replace('name = "adr"', 'name = "adr"\nextends = "ghost"')
    with pytest.raises(S.DocTypeError, match=r"l/a.toml:\d+: unknown document type 'ghost'"):
        S.resolve([("l", [("a.toml", lone)])])


def test_shipped_types_load_into_the_registry():
    reg = S.registry()
    assert "adr" in reg and "runbook" in reg
    assert reg["adr"].expand(seq=1, slug="x") == "docs/adr/0001-x.md"
    assert reg["adr"].contract == "point_in_time"


def test_read_layer_missing_dir(tmp_path):
    assert S.read_layer("project", tmp_path / "none") == ("project", [])
    (tmp_path / "a.toml").write_text(ADR)
    assert S.read_layer("project", tmp_path)[1][0][0] == "a.toml"


@pytest.mark.parametrize("pat", ["docs/{}.md", "docs/{a-b}.md", "docs/{seq:{w}}.md", "docs/{seq"])
def test_unexpandable_pattern_is_refused(pat):
    with pytest.raises(S.DocTypeError, match="placeholder"):
        S.parse(ADR.replace("docs/adr/{seq:04}-{slug}.md", pat))


def test_same_layer_duplicate_name_is_refused_not_recursed():
    b = ADR.replace('name = "adr"', 'name = "adr"\nextends = "adr"')
    with pytest.raises(S.DocTypeError, match="defined twice"):
        S.resolve([("l", [("a.toml", ADR), ("b.toml", b)])])


def test_section_type_error_points_at_its_own_section():
    text = ADR.replace("order = 2", 'order = 2\nlevel = "2"')
    with pytest.raises(S.DocTypeError, match=r"x\.toml:13: .*level"):
        S.parse(text, source="x.toml")


@pytest.mark.parametrize("val", ["5", '["docs/{slug}.md"]'])
def test_non_string_pattern_is_a_file_error(val):
    text = ADR.replace('pattern = "docs/adr/{seq:04}-{slug}.md"', f"pattern = {val}")
    with pytest.raises(S.DocTypeError, match=r"x\.toml:\d+: .*pattern must be str"):
        S.parse(text, source="x.toml")


def test_extending_type_refuses_bad_tables_and_duplicate_sections():
    base = ("shipped", [("adr.toml", ADR)])
    child = 'name = "c"\nextends = "adr"\n'
    for body, msg in (
        ("review = true\n", "review must be a table"),
        (
            '[[doctype.section]]\nid = "z"\n[[doctype.section]]\nid = "z"\n',
            "duplicate section id 'z'",
        ),
    ):
        text = "[doctype]\n" + child + body
        with pytest.raises(S.DocTypeError, match=msg):
            S.resolve([base, ("project", [("c.toml", text)])])


@pytest.mark.parametrize("bad", ["section = 5", "section = true", "review = 1", "validate = 1"])
def test_wrong_shaped_tables_are_file_errors_in_every_layer(bad):
    base = ("shipped", [("adr.toml", ADR)])
    text = '[doctype]\nname = "c"\nextends = "adr"\n' + bad + "\n"
    with pytest.raises(S.DocTypeError, match=r"project/c\.toml:\d+:"):
        S.resolve([base, ("project", [("c.toml", text)])])
