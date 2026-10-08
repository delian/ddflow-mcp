"""B-uni-record-surface.1-api: one descriptor, seven verbs, for every family of records.

`api.records` answers list / show / add / edit / remove / search / revise for any
`RecordKind`; these tests use the real rule descriptor and a second, made-up kind so that
nothing here is true of rules only.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.api import _dedupe as DD
from ddflow.api import records as R
from ddflow.core import outcome as O

NOTE = R.RecordKind(
    name="note",
    def_kind="skill",
    summary="a note",
    fields=(
        R.FieldSpec("title", required=True),
        R.FieldSpec("body", default=""),
        R.FieldSpec("tags", "array", default=()),
        R.FieldSpec("level", choices=("low", "high"), default="low"),
        R.FieldSpec("weight", "integer"),
        R.FieldSpec("pinned", "boolean"),
    ),
    text=("body", "tags"),
    columns=("title", "level", "tags"),
    filters=("tags", "level"),
)


def _add(repo, rid="alpha", **fields):
    fields.setdefault("title", f"title of {rid}")
    return R.record_add(repo, NOTE, rid, fields)


# -- the descriptor ------------------------------------------------------------------


def test_a_descriptor_naming_a_field_it_lacks_is_refused():
    with pytest.raises(ValueError, match="no such field"):
        R.RecordKind(
            name="x",
            def_kind="skill",
            summary="",
            fields=(R.FieldSpec("title"),),
            columns=("nope",),
        )


def test_a_descriptor_needs_a_def_kind_unless_the_family_supplies_every_verb():
    with pytest.raises(ValueError, match="not a def kind"):
        R.RecordKind(name="x", def_kind="nosuch", summary="", fields=(R.FieldSpec("title"),))
    own = {v: lambda repo, kind, **kw: O.ok("x") for v in R.VERBS}
    R.RecordKind(name="x", def_kind="", summary="", fields=(R.FieldSpec("title"),), ops=own)


def test_a_field_type_and_choices_are_checked_when_declared():
    with pytest.raises(ValueError, match="type"):
        R.FieldSpec("a", "float")
    with pytest.raises(ValueError, match="only a string"):
        R.FieldSpec("a", "integer", choices=("1",))


def test_two_families_cannot_declare_one_name():
    other = R.RecordKind(name="rule", def_kind="skill", summary="", fields=(R.FieldSpec("title"),))
    with pytest.raises(ValueError, match="already declared"):
        R.declare(other)
    assert R.declare(R.RULE) is R.RULE  # the same declaration again is harmless


def test_the_rule_kind_is_a_rule_definition():
    assert R.KINDS["rule"].def_kind == "rule"
    assert R.KINDS["rule"].aliases == ("rules",)


# -- add / show -----------------------------------------------------------------------


def test_add_files_a_record_with_its_defaults_and_show_reads_it_back(repo):
    out = _add(repo, body="check it")
    assert out.exit == O.OK and out.data["id"] == "alpha" and out.data["record_kind"] == "note"
    shown = R.record_show(repo, NOTE, "alpha")
    assert shown.exit == O.OK
    assert shown.data["fields"] == {
        "title": "title of alpha",
        "body": "check it",
        "level": "low",
        "tags": [],
    }
    assert shown.data["history_total"] == 1


def test_add_refuses_an_id_already_filed_whatever_its_status(repo):
    _add(repo)
    assert _add(repo).exit == O.REFUSED
    R.record_remove(repo, NOTE, "alpha", reason="gone")
    again = _add(repo)
    assert again.exit == O.REFUSED and "retired" in again.reason


def test_add_checks_the_fields_before_writing(repo):
    assert R.record_add(repo, NOTE, "a", {"body": "x"}).exit == O.FAIL  # title is required
    assert R.record_add(repo, NOTE, "a", {"title": "t", "level": "mid"}).exit == O.FAIL
    assert R.record_add(repo, NOTE, "a", {"title": "t", "weight": True}).exit == O.FAIL
    assert R.record_add(repo, NOTE, "a", {"title": "t", "tags": "x"}).exit == O.FAIL
    assert R.record_add(repo, NOTE, "a", {"title": "t", "nope": 1}).exit == O.FAIL
    assert R.record_list(repo, NOTE).exit == O.NOTHING


def test_show_of_an_unknown_record_fails_naming_it(repo):
    out = R.record_show(repo, NOTE, "ghost")
    assert out.exit == O.FAIL and "ghost" in out.reason


def test_show_cuts_a_long_history_to_the_newest_entries(repo):
    _add(repo)
    for i in range(R.HISTORY_SHOWN + 5):
        assert R.record_edit(repo, NOTE, "alpha", {"weight": i}).exit == O.OK
    shown = R.record_show(repo, NOTE, "alpha")
    assert len(shown.data["history"]) == R.HISTORY_SHOWN
    assert shown.data["history_total"] == R.HISTORY_SHOWN + 6
    assert shown.data["fields"]["weight"] == R.HISTORY_SHOWN + 4


FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"


def test_add_runs_the_duplicate_check_for_a_kind_in_the_dedupe_list(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")  # conftest turns it off elsewhere
    assert run_cli(repo, "init")[0] == 0
    code, out, err = run_cli(
        repo, "decision", "add", "--id", "D-old", "--title", FIRST, "--decision", FIRST
    )
    assert code == 0, (out, err)
    fields = {"title": SECOND, "content": SECOND}
    twin = R.record_add(repo, R.RULE, "r-new", fields, agent="a")
    assert twin.exit == O.REFUSED and twin.data["candidates"][0]["id"] == "D-old"
    checked = R.record_add(repo, R.RULE, "r-new", fields, answer=DD.Answer("new"), agent="a")
    assert checked.exit == O.OK and checked.data["record_kind"] == "rule"


# -- edit / remove / revise -------------------------------------------------------------


def test_edit_changes_the_fields_named_and_keeps_the_rest(repo):
    _add(repo, body="keep me", level="high")
    out = R.record_edit(repo, NOTE, "alpha", {"title": "new title"})
    assert out.exit == O.OK and out.data["changed"] == ["title"]
    fields = R.record_show(repo, NOTE, "alpha").data["fields"]
    assert (
        fields["title"] == "new title" and fields["body"] == "keep me" and fields["level"] == "high"
    )


def test_edit_with_a_null_removes_an_optional_field_but_not_a_required_one(repo):
    _add(repo, weight=3)
    assert R.record_edit(repo, NOTE, "alpha", {"weight": None}).exit == O.OK
    assert "weight" not in R.record_show(repo, NOTE, "alpha").data["fields"]
    assert R.record_edit(repo, NOTE, "alpha", {"title": None}).exit == O.FAIL


def test_edit_says_nothing_when_nothing_is_named_or_changed(repo):
    _add(repo)
    assert R.record_edit(repo, NOTE, "alpha", {}).exit == O.NOTHING
    assert R.record_edit(repo, NOTE, "alpha", {"title": "title of alpha"}).exit == O.NOTHING


def test_edit_of_a_missing_or_retired_record_is_refused(repo):
    assert R.record_edit(repo, NOTE, "ghost", {"body": "x"}).exit == O.REFUSED
    _add(repo)
    R.record_remove(repo, NOTE, "alpha", reason="done")
    assert R.record_edit(repo, NOTE, "alpha", {"body": "x"}).exit == O.REFUSED


def test_remove_retires_with_a_reason_and_the_record_stays(repo):
    _add(repo)
    assert R.record_remove(repo, NOTE, "alpha", reason=" ").exit == O.FAIL
    assert R.record_remove(repo, NOTE, "alpha", reason="obsolete").exit == O.OK
    shown = R.record_show(repo, NOTE, "alpha").data
    assert shown["status"] == "retired" and shown["status_reason"] == "obsolete"
    assert R.record_list(repo, NOTE).exit == O.NOTHING
    assert R.record_list(repo, NOTE, all=True).data["total"] == 1


def test_revise_replaces_the_whole_record_keeps_the_reason_and_brings_it_back(repo):
    _add(repo, body="old", level="high")
    R.record_remove(repo, NOTE, "alpha", reason="obsolete")
    assert R.record_revise(repo, NOTE, "alpha", {"title": "t2"}, reason="").exit == O.FAIL
    out = R.record_revise(repo, NOTE, "alpha", {"title": "t2"}, reason="it matters again")
    assert out.exit == O.OK and out.data["replaced"] is True
    shown = R.record_show(repo, NOTE, "alpha").data
    assert shown["status"] == "active"
    assert shown["fields"] == {"title": "t2", "body": "", "level": "low", "tags": []}
    assert shown["provenance"]["revised"] == "it matters again"


def test_revise_of_an_unknown_id_is_refused_and_points_at_add(repo):
    out = R.record_revise(repo, NOTE, "ghost", {"title": "t"}, reason="r")
    assert out.exit == O.REFUSED and "add" in out.reason


# -- list --------------------------------------------------------------------------------


def test_list_gives_the_declared_columns_sorted_by_id(repo):
    _add(repo, "b", level="high", tags=["x"])
    _add(repo, "a", body="b")
    out = R.record_list(repo, NOTE)
    assert [r["id"] for r in out.data["rows"]] == ["a", "b"]
    assert out.data["rows"][1] == {
        "id": "b",
        "status": "active",
        "title": "title of b",
        "level": "high",
        "tags": ["x"],
    }
    assert "body" not in out.data["rows"][0]


def test_list_narrows_by_a_declared_filter_and_refuses_another(repo):
    _add(repo, "a", tags=["x", "y"])
    _add(repo, "b", level="high")
    assert [r["id"] for r in R.record_list(repo, NOTE, filters={"tags": "y"}).data["rows"]] == ["a"]
    assert [r["id"] for r in R.record_list(repo, NOTE, filters={"level": "high"}).data["rows"]] == [
        "b"
    ]
    bad = R.record_list(repo, NOTE, filters={"body": "q"})
    assert bad.exit == O.REFUSED and "filters are tags, level" in bad.reason
    assert (
        R.record_list(repo, NOTE, filters={"level": ""}).data["total"] == 2
    )  # an empty value is no filter


def test_list_is_bounded_and_says_so_with_exact_totals(repo):
    for i in range(5):
        _add(repo, f"n{i}")
    out = R.record_list(repo, NOTE, limit=2)
    assert (out.data["total"], out.data["shown"], out.data["truncated"]) == (5, 2, True)
    assert [r["id"] for r in out.data["rows"]] == ["n0", "n1"]
    assert R.record_list(repo, NOTE).data["limit"] == R.DEFAULT_LIMIT
    assert R.record_list(repo, NOTE, limit=10**6).data["limit"] == R.MAX_LIMIT
    assert R.record_list(repo, NOTE, limit=-1).exit == O.REFUSED


def test_a_long_text_in_a_row_is_cut_but_show_has_it_whole(repo):
    _add(repo, title="t" * 400)
    row = R.record_list(repo, NOTE).data["rows"][0]
    assert len(row["title"]) <= R.CELL_MAX + len(" [...]") and row["title"].endswith("[...]")
    assert R.record_show(repo, NOTE, "alpha").data["fields"]["title"] == "t" * 400


# -- search ------------------------------------------------------------------------------


@pytest.fixture
def corpus(repo):
    _add(repo, "cache", title="Cache invalidation", body="clear the build cache before release")
    _add(repo, "naming", title="Naming things", body="use plain words", tags=["style"])
    _add(repo, "retired", title="Cache retired", body="obsolete cache note")
    R.record_remove(repo, NOTE, "retired", reason="old")
    return repo


def test_ranked_search_puts_the_best_match_first_and_skips_retired_ones(corpus):
    out = R.record_search(corpus, NOTE, "clear the build cache")
    assert [r["id"] for r in out.data["rows"]] == ["cache"]
    assert out.data["rows"][0]["score"] > 0 and out.data["searched"] == 2
    with_old = R.record_search(corpus, NOTE, "cache", all=True)
    assert {r["id"] for r in with_old.data["rows"]} == {"cache", "retired"}


def test_exact_and_regex_search_match_the_text_not_the_meaning(corpus):
    assert R.record_search(corpus, NOTE, "PLAIN  words", mode="exact").data["total"] == 1
    assert R.record_search(corpus, NOTE, "plain wor", mode="exact").data["total"] == 1
    assert R.record_search(corpus, NOTE, "plain meaning", mode="exact").exit == O.NOTHING
    assert R.record_search(corpus, NOTE, r"cache\s+before", mode="regex").data["total"] == 1
    assert R.record_search(corpus, NOTE, "style", mode="exact").data["total"] == 1  # tags are text


def test_a_regex_that_could_take_exponential_time_is_refused_with_the_reason(corpus):
    out = R.record_search(corpus, NOTE, "(a+)+$", mode="regex")
    assert out.exit == O.REFUSED and out.reason


def test_search_refuses_what_it_cannot_run(corpus):
    assert R.record_search(corpus, NOTE, "  ").exit == O.REFUSED
    assert R.record_search(corpus, NOTE, "cache", mode="fuzzy").exit == O.REFUSED
    assert (
        R.record_search(corpus, NOTE, "the of", mode="ranked").exit == O.REFUSED
    )  # only stop words
    assert R.record_search(corpus, NOTE, "cache", filters={"body": "x"}).exit == O.REFUSED


def test_search_narrows_by_filter_and_is_bounded(corpus):
    out = R.record_search(corpus, NOTE, "words style cache", filters={"tags": "style"})
    assert [r["id"] for r in out.data["rows"]] == ["naming"]
    out = R.record_search(corpus, NOTE, "cache", all=True, limit=1)
    assert (out.data["total"], out.data["shown"], out.data["truncated"]) == (2, 1, True)


# -- verbs a family does not offer, or supplies itself ------------------------------------


def test_a_verb_the_kind_does_not_offer_is_refused_by_name(repo):
    readonly = R.RecordKind(
        name="ro",
        def_kind="skill",
        summary="",
        fields=(R.FieldSpec("title"),),
        verbs=("list", "show"),
    )
    out = R.record_add(repo, readonly, "a", {"title": "t"})
    assert out.exit == O.REFUSED and "no 'add'" in out.reason and "list, show" in out.reason


def test_a_family_supplies_a_verb_of_its_own_and_the_rest_stay_generic(repo):
    seen = {}

    def own_list(repo, kind, **kw):
        seen.update(kw)
        return O.ok("quota.list", rows=[{"id": "q"}])

    quota = R.RecordKind(
        name="quota",
        def_kind="skill",
        summary="",
        fields=(R.FieldSpec("title"),),
        ops={"list": own_list},
    )
    out = R.record_list(repo, quota, filters={"a": "b"}, limit=3)
    assert out.data["rows"] == [{"id": "q"}]
    assert seen == {"filters": {"a": "b"}, "all": False, "limit": 3, "agent": ""}
    assert R.record_add(repo, quota, "q", {"title": "t"}).exit == O.OK
    with pytest.raises(ValueError, match="not offered"):
        R.RecordKind(
            name="q2",
            def_kind="skill",
            summary="",
            fields=(R.FieldSpec("title"),),
            verbs=("list",),
            ops={"add": own_list},
        )
