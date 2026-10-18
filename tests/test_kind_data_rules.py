"""SE-kind-data.2: lifecycle, links, sources, validators, render and stamp declared as data
and enforced when a record is added, edited or revised."""

from __future__ import annotations

import pytest

import ddflow.api.record_kind_data as KDA
import ddflow.api.records as R
import ddflow.core.defs as D
import ddflow.core.kinddata as KD

RISK = """
name = "riskx"
summary = "a risk"
text = ["note"]
columns = ["title", "state"]
sources = ["origin"]
render = "{id}: {title} [{state}] x{score}"
stamp = 2

[lifecycle]
field = "state"
initial = "open"
[lifecycle.transitions]
open = ["mitigated", "accepted"]
mitigated = ["closed"]

[[link]]
field = "owner"
to = "stakeholderx"

[[link]]
field = "related"
to = "riskx"
many = true

[[validator]]
field = "score"
min = 1
max = 5

[[validator]]
field = "origin"
pattern = "^https?://"
message = "origin must be a URL"

[[field]]
name = "title"
required = true
[[field]]
name = "note"
default = ""
[[field]]
name = "state"
[[field]]
name = "origin"
[[field]]
name = "score"
type = "integer"
[[field]]
name = "owner"
[[field]]
name = "related"
type = "array"
[[field]]
name = "tags"
type = "array"
"""

STAKE = 'name = "stakeholderx"\nsummary = "s"\n[[field]]\nname = "title"\nrequired = true\n'


@pytest.fixture
def kinds():
    try:
        yield KDA.parse_kind(RISK), KDA.parse_kind(STAKE)
    finally:
        for k in ("riskx", "stakeholderx"):
            D.DEF_KINDS.pop(k, None)


def _add(repo, kind, rid="r1", **kw):
    kw.setdefault("title", rid)
    kw.setdefault("origin", "https://x.example/a")
    return R.record_add(repo, kind, rid, kw)


def test_rules_parse_and_stamp(kinds):
    risk, _ = kinds
    assert risk.rules.stamp == 2 and risk.rules.lifecycle.initial == "open"
    assert risk.rules.lifecycle.states == ("open", "mitigated", "accepted", "closed")
    assert [x.field for x in risk.rules.links] == ["owner", "related"]


def test_new_record_starts_in_the_initial_state(kinds, repo):
    risk, _ = kinds
    assert _add(repo, risk).exit == 0
    assert R.record_show(repo, risk, "r1").data["fields"]["state"] == "open"
    bad = _add(repo, risk, "r2", state="closed")
    assert bad.exit != 0 and "starts state 'open'" in bad.reason


def test_state_moves_follow_the_transitions(kinds, repo):
    risk, _ = kinds
    _add(repo, risk)
    assert R.record_edit(repo, risk, "r1", {"state": "closed"}).exit != 0
    assert R.record_edit(repo, risk, "r1", {"state": "mitigated"}).exit == 0
    out = R.record_edit(repo, risk, "r1", {"state": "open"})
    assert out.exit != 0 and "allowed: closed" in out.reason
    assert R.record_edit(repo, risk, "r1", {"state": "closed"}).exit == 0
    assert "final" in R.record_edit(repo, risk, "r1", {"state": "open"}).reason
    assert R.record_edit(repo, risk, "r1", {"title": "kept"}).exit == 0  # no state change


def test_revise_keeps_the_state_and_cannot_jump_it(kinds, repo):
    risk, _ = kinds
    _add(repo, risk)
    R.record_edit(repo, risk, "r1", {"state": "mitigated"})
    out = R.record_revise(repo, risk, "r1", {"title": "t", "origin": "http://o"}, reason="why")
    assert out.exit == 0  # replaced whole, but it stays where it was in its life
    assert R.record_show(repo, risk, "r1").data["fields"]["state"] == "mitigated"
    out = R.record_revise(
        repo, risk, "r1", {"title": "t", "origin": "http://o", "state": "open"}, reason="why"
    )
    assert out.exit != 0 and "cannot go from 'mitigated' to 'open'" in out.reason


def test_a_source_is_required_and_validated(kinds, repo):
    risk, _ = kinds
    out = R.record_add(repo, risk, "r1", {"title": "t"})
    assert out.exit != 0 and "origin is required" in out.reason
    out = _add(repo, risk, origin="nowhere")
    assert "origin must be a URL" in out.reason
    _add(repo, risk)
    assert "origin is required" in R.record_edit(repo, risk, "r1", {"origin": ""}).reason


def test_bounds_validator(kinds, repo):
    risk, _ = kinds
    assert "at least 1" in _add(repo, risk, score=0).reason
    assert "at most 5" in _add(repo, risk, score=6).reason
    assert _add(repo, risk, score=5).exit == 0


def test_links_must_name_live_records(kinds, repo):
    risk, stake = kinds
    assert "no stakeholderx 'ann'" in _add(repo, risk, owner="ann").reason
    assert R.record_add(repo, stake, "ann", {"title": "Ann"}).exit == 0
    assert _add(repo, risk, owner="ann").exit == 0
    assert "no riskx 'ghost'" in _add(repo, risk, "r2", related=["r1", "ghost"]).reason
    assert _add(repo, risk, "r2", related=["r1"]).exit == 0
    R.record_remove(repo, stake, "ann", reason="gone")
    assert "no stakeholderx" in R.record_edit(repo, risk, "r1", {"owner": "ann"}).reason


def test_show_renders_the_template(kinds, repo):
    risk, _ = kinds
    _add(repo, risk, score=3)
    assert R.record_show(repo, risk, "r1").data["rendered"] == "r1: r1 [open] x3"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ('field = "state"\ninitial', "lifecycle names no such field"),
        ('field = "owner"\nto = "stakeholderx"', "link field 'related'"),
        ('render = "{id}: {title}', "render names no such field"),
        ('pattern = "^https?://"', "bad pattern"),
    ],
)
def test_declarations_are_checked_against_the_fields(edit, message):
    text = RISK
    if message.startswith("lifecycle"):
        text = text.replace('field = "state"\ninitial', 'field = "nostate"\ninitial')
    elif message.startswith("link"):
        text = text.replace('field = "owner"', 'field = "related"')
    elif message.startswith("render"):
        text = text.replace('render = "{id}: {title}', 'render = "{id}: {nope} {title}')
    else:
        text = text.replace('pattern = "^https?://"', 'pattern = "(["')
    try:
        with pytest.raises(ValueError, match=message):
            KDA.parse_kind(text)
    finally:
        D.DEF_KINDS.pop("riskx", None)


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("[lifecycle]\nfield = 1", "lifecycle needs field and initial"),
        ("[[link]]\nfield = 'a'", "link #1 needs field and to"),
        ("[[validator]]\nfield = 'a'\nmin = 'x'", "min must be a whole number"),
        ("sources = 'a'", "sources must be a list"),
        ("stamp = 'x'", "stamp must be a whole number"),
        ("[[validator]]\nbogus = 1", "unknown key"),
    ],
)
def test_malformed_rules_are_refused(snippet, message):
    head, _, tail = STAKE.partition("[[field]]")
    with pytest.raises(KD.KindDataError, match=message):
        KD.parse(head + snippet + "\n[[field]]" + tail)


def test_a_pattern_is_not_bypassed_by_a_long_value():
    import ddflow.core.kindrules as KR

    v = KR.Validator("slug", pattern="^[a-z]+$")
    assert KR._value_problem(v, "abc") == ""
    assert KR._value_problem(v, "a" * 20000 + "!") != ""


def test_an_unnumbered_render_field_is_refused_when_declared():
    with pytest.raises(ValueError, match="render names no such field"):
        KDA.parse_kind(STAKE.replace('summary = "s"', 'summary = "s"\nrender = "{}"'))
    D.DEF_KINDS.pop("stakeholderx", None)


def test_show_renders_status_as_the_definition_status(kinds, repo):
    risk, _ = kinds
    import dataclasses

    risk = dataclasses.replace(
        risk, rules=dataclasses.replace(risk.rules, render="{id} is {status}")
    )
    _add(repo, risk)
    assert R.record_show(repo, risk, "r1").data["rendered"] == "r1 is active"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (
            "sources = ['n']\n[[field]]\nname='title'\n[[field]]\nname='n'\ntype='integer'",
            "sources field 'n' is a integer",
        ),
        (
            "[[validator]]\nfield='n'\npattern='^1'\n[[field]]\nname='title'\n[[field]]\nname='n'\ntype='integer'",
            "validator text check field 'n'",
        ),
        (
            "[lifecycle]\nfield='n'\ninitial='a'\ntransitions=5\n[[field]]\nname='n'",
            "transitions must be a table",
        ),
    ],
)
def test_rules_that_cannot_work_are_refused(extra, message):
    head = 'name = "zz"\nsummary = "s"\n'
    try:
        with pytest.raises((ValueError, KD.KindDataError), match=message):
            KDA.parse_kind(head + extra)
    finally:
        D.DEF_KINDS.pop("zz", None)


def test_a_field_called_status_wins_in_the_template(repo):
    text = (
        'name = "bugx"\nsummary = "b"\nrender = "{id} is {status}"\n'
        "[lifecycle]\nfield = 'status'\ninitial = 'new'\n[lifecycle.transitions]\nnew = ['fixed']\n"
        "[[field]]\nname = 'title'\n[[field]]\nname = 'status'\n"
    )
    try:
        bug = KDA.parse_kind(text)
        R.record_add(repo, bug, "b1", {"title": "t"})
        R.record_edit(repo, bug, "b1", {"status": "fixed"})
        assert R.record_show(repo, bug, "b1").data["rendered"] == "b1 is fixed"
    finally:
        D.DEF_KINDS.pop("bugx", None)


def test_a_bound_and_a_text_check_on_one_field_cannot_both_work():
    extra = "[[validator]]\nfield='n'\nmin=1\npattern='^1'\n[[field]]\nname='title'\n[[field]]\nname='n'\ntype='integer'"
    try:
        with pytest.raises(ValueError, match="validator text check field 'n'"):
            KDA.parse_kind('name = "zz"\nsummary = "s"\n' + extra)
    finally:
        D.DEF_KINDS.pop("zz", None)
