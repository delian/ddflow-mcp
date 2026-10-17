"""Record kinds declared as data (SE-kind-data.1): the TOML descriptor, its checks, and the
rule kind migrated onto it without a change in behaviour."""

from __future__ import annotations

import pytest

import ddflow.api.record_kind_data as KDA
import ddflow.api.records as R
import ddflow.core.defs as D
import ddflow.core.kinddata as KD

MINIMAL = """
name = "gizmo"
summary = "a gizmo"
text = ["note"]
columns = ["title", "colour"]
filters = ["colour"]
[[field]]
name = "title"
required = true
[[field]]
name = "note"
default = ""
[[field]]
name = "colour"
choices = ["red", "blue"]
default = "red"
[[field]]
name = "tags"
type = "array"
default = ["a"]
"""


def test_descriptor_builds_the_kind():
    kind = KDA.parse_kind(MINIMAL)
    try:
        assert kind.name == "gizmo" and kind.def_kind == "gizmo"
        assert D.DEF_KINDS["gizmo"] == "a gizmo"
        assert kind.field("colour").choices == ("red", "blue")
        assert kind.field("tags").default == ("a",)
        assert kind.verbs == R.VERBS and kind.search and kind.recall
    finally:
        D.DEF_KINDS.pop("gizmo", None)


def test_verbs_not_offered_are_declined_with_the_reason():
    text = MINIMAL.replace(
        'name = "gizmo"', 'name = "gizmo"\nverbs = ["list", "show"]\ndeclined_other = "later"'
    )
    try:
        kind = KDA.parse_kind(text)
        assert kind.verbs == ("list", "show")
        assert set(kind.declined) == set(R.VERBS) - {"list", "show"}
        assert set(kind.declined.values()) == {"later"}
    finally:
        D.DEF_KINDS.pop("gizmo", None)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ('name = "gizmo"', "name is required"),
        ('[[field]]\nname = "title"\nrequired = true', "at least one [[field]]"),
        ('summary = "a gizmo"', "summary is required"),
        ('name = "gizmo"\nops = 1', "ops is code"),
        ('name = "gizmo"\nbogus = 1', "unknown key(s) bogus"),
        ('type = "array"', "field 'tags'"),
    ],
)
def test_bad_descriptor_names_the_problem(edit, message):
    text = MINIMAL
    if message == "name is required":
        text = text.replace('name = "gizmo"', "")
    elif message.startswith("at least"):
        text = 'name = "g"\nsummary = "s"'
    elif message == "summary is required":
        text = text.replace(edit, "")
    elif message.startswith("field"):
        text = text.replace(edit, 'type = "tensor"')
    else:
        text = text.replace('name = "gizmo"', edit)
    with pytest.raises(
        KD.KindDataError, match=message.replace("[", r"\[").replace("(", r"\(").replace(")", r"\)")
    ):
        KD.parse(text)


def test_not_toml_is_refused():
    with pytest.raises(KD.KindDataError, match="not TOML"):
        KD.parse("name = ", source="x.toml")


def test_a_bad_field_reference_is_refused_by_the_kind():
    with pytest.raises(ValueError, match="no such field"):
        KDA.parse_kind(MINIMAL.replace('filters = ["colour"]', 'filters = ["nope"]'))


def test_rule_is_data_and_declared():
    assert R.KINDS["rule"] is R.RULE
    assert R.RULE.aliases == ("rules",) and R.RULE.def_kind == "rule"
    assert R.RULE.verbs == R.VERBS[:7]
    assert set(R.RULE.declined) == set(R.VERBS[7:])
    assert not R.RULE.recall and not R.RULE.resource
    assert [f.name for f in R.RULE.fields] == [
        "title",
        "content",
        "tags",
        "scope",
        "priority",
        "globs",
    ]
    assert R.RULE.field("priority").default == 50 and R.RULE.field("tags").default == ()


def test_every_packaged_descriptor_loads():
    names = [n for n, _ in KDA.packaged()]
    assert "rule" in names
    for name, text in KDA.packaged():
        assert KDA.parse_kind(text, source=name).name == name
        assert KDA.load_packaged(name).name == name


def test_register_kind_never_redefines_a_builtin():
    D.register_kind("rule", "something else")
    assert D.DEF_KINDS["rule"].startswith("a project rule")


@pytest.mark.parametrize("first", ["ddflow.api.record_kind_data", "ddflow.api.records"])
def test_either_module_imports_first(first):
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", f"import {first}"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_failed_descriptor_leaves_no_def_kind():
    bad = MINIMAL.replace('filters = ["colour"]', 'filters = ["nope"]').replace("gizmo", "widget")
    with pytest.raises(ValueError, match="no such field"):
        KDA.parse_kind(bad)
    assert "widget" not in D.DEF_KINDS


def test_kind_from_data_refuses_code_keys():
    desc = KD.parse(MINIMAL)
    with pytest.raises(KD.KindDataError, match="ops is code"):
        KDA.kind_from_data({**desc, "ops": {}})


def test_declined_must_be_a_table_and_a_typo_verb_is_refused():
    with pytest.raises(KD.KindDataError, match="declined must be a table"):
        KD.parse(MINIMAL.replace('name = "gizmo"', 'name = "gizmo"\ndeclined = 3'))
    with pytest.raises(ValueError, match="unknown verb 'histroy'"):
        KDA.parse_kind(MINIMAL + '\n[declined]\nhistroy = "x"\n')
