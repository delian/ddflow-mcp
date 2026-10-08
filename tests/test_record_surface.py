"""B-uni-record-surface.2-surface: a RecordKind generates its CLI verbs and its one MCP tool.

Nothing is wired into the real CLI or tool table yet, so these tests build a parser and a
tool entry from the generated `Command`s and drive them the way a user and an agent would.
"""

from __future__ import annotations

import argparse
import dataclasses
import json

import pytest
from conftest import run_cli

from ddflow.api import records as R
from ddflow.core import outcome as O
from ddflow.surfaces import records as S
from ddflow.surfaces.context import Ctx
from ddflow.surfaces.registry import (
    AS_AGENT,
    SuggestingParser,
    add_commands,
    exempt_paths,
    routed_paths,
    used_aliases,
)

NOTE = R.RecordKind(
    name="note",
    def_kind="skill",
    summary="a note an agent keeps",
    fields=(
        R.FieldSpec("title", help="One line.", required=True),
        R.FieldSpec("body", default=""),
        R.FieldSpec("tags", "array", "Labels."),
        R.FieldSpec("level", choices=("low", "high"), default="low"),
        R.FieldSpec("weight", "integer"),
        R.FieldSpec("pinned", "boolean"),
    ),
    text=("body", "tags"),
    columns=("title", "level"),
    filters=("tags", "level"),
    aliases=("notes",),
)


def _parser(kind=NOTE):
    p = SuggestingParser(prog="ddflow")
    p.add_argument("--repo")
    p.add_argument("--agent", default="")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    add_commands(
        sub,
        S.record_commands(kind),
        groups=S.record_groups(kind),
        group_aliases=S.record_group_aliases(kind, "0.1.17"),
    )
    return p


@pytest.fixture
def cli(repo, capsys):
    """Run one generated command line against ``repo``: ``(exit, stdout, stderr)``."""

    def run(*argv: str, json_out: bool = False, kind=NOTE):
        flags = ["--repo", str(repo), *(["--json"] if json_out else [])]
        a = _parser(kind).parse_args([*flags, *argv])
        code = a.fn(a, Ctx(a))
        got = capsys.readouterr()
        return code, got.out, got.err

    return run


def _tool(kind=NOTE):
    return S.record_commands(kind)[0]


# -- what is generated ---------------------------------------------------------------------


def test_a_kind_generates_one_tool_then_a_command_per_verb():
    cmds = S.record_commands(NOTE)
    assert cmds[0].tool == "ddflow_note" and cmds[0].path == ()
    assert [c.path for c in cmds[1:]] == [("note", v) for v in R.VERBS]
    assert routed_paths(cmds) == {("note", v): ("ddflow_note", v) for v in R.VERBS}
    assert exempt_paths(cmds) == {}  # every CLI verb is served by the tool


def test_the_tool_schema_has_the_verb_enum_and_every_field_but_no_cli_flag():
    schema = _tool().input_schema()
    props = schema["properties"]
    assert schema["required"] == ["verb"] and schema["additionalProperties"] is False
    assert props["verb"]["enum"] == list(R.VERBS)
    assert props["tags"] == {"type": "array", "description": "Labels.", "items": {"type": "string"}}
    assert props["weight"]["type"] == "integer" and props["pinned"]["type"] == "boolean"
    for cli_only in ("new", "extends", "duplicate_of", "related", "check"):
        assert cli_only not in props
    assert {"relation", "check_only", "where", "unset", "reason", AS_AGENT} <= set(props)


def test_the_tool_listing_is_small_enough_to_be_one_tool_per_kind():
    assert len(json.dumps(_tool().tool_listing(), separators=(",", ":"))) < 2500


def test_a_verb_command_carries_only_the_parameters_its_verb_takes():
    by = {c.path[1]: {p.name for p in c.params} for c in S.record_commands(NOTE)[1:]}
    fields = {f.name for f in NOTE.fields}
    assert by["list"] == {"where", "all", "limit"}
    assert by["show"] == {"id"}
    assert by["add"] == {"id", *fields, "new", "extends", "duplicate_of", "related", "check"}
    assert by["edit"] == {"id", "unset", *fields}
    assert by["remove"] == {"id", "reason"}
    assert by["search"] == {"query", "mode", "where", "all", "limit"}
    assert by["revise"] == {"id", "reason", *fields}


def test_a_field_may_not_take_a_name_the_surface_uses():
    bad = R.RecordKind(
        name="x",
        def_kind="skill",
        summary="s",
        fields=(R.FieldSpec("title"), R.FieldSpec("reason")),
    )
    with pytest.raises(ValueError, match="reason"):
        S.record_commands(bad)


def test_a_kind_offering_fewer_verbs_generates_fewer_commands():
    ro = R.RecordKind(
        name="ro",
        def_kind="skill",
        summary="s",
        fields=(R.FieldSpec("title"),),
        verbs=("list", "show"),
    )
    cmds = S.record_commands(ro)
    assert [c.path for c in cmds[1:]] == [("ro", "list"), ("ro", "show")]
    assert _tool(ro).input_schema()["properties"]["verb"]["enum"] == ["list", "show"]
    with pytest.raises(ValueError, match="verb must be one of list, show"):
        S.dispatch(".", ro, {"verb": "add", "id": "a"})


def test_the_help_of_a_verb_names_its_flags(capsys):
    expected = {
        "add": ("--title", "--tags", "--weight", "--pinned", "--no-pinned", "--extends", "--check"),
        "list": ("--where", "--all", "--limit"),
        "remove": ("--reason",),
        "search": ("--mode", "--where"),
    }
    for verb, flags in expected.items():
        with pytest.raises(SystemExit) as stop:
            _parser().parse_args(["note", verb, "--help"])
        shown = capsys.readouterr().out
        assert stop.value.code == 0 and all(f in shown for f in flags), (verb, shown)


def test_the_answer_flags_are_those_of_dedupe_flags_with_the_same_help():
    from ddflow.surfaces import dedupe_flags

    shared = argparse.ArgumentParser()
    dedupe_flags.add_flags(shared)
    theirs = {a.dest: (a.option_strings, a.help) for a in shared._actions if a.dest != "help"}
    add = next(c for c in S.record_commands(NOTE) if c.path == ("note", "add"))
    mine = {}
    for p in add.params:
        if p.cli_only:
            mine[p.name] = ([p.option], p.help)
    assert mine == theirs


def test_a_reason_for_a_missing_remove_reason_names_the_record(repo):
    out = _tool().tool_entry()["api"](repo, {"verb": "remove", "id": "x"}, "ag")
    assert out.exit == O.FAIL and "retiring a note" in out.reason and "definition" not in out.reason


def test_a_field_may_not_be_called_as_agent():
    bad = R.RecordKind(
        name="x",
        def_kind="skill",
        summary="s",
        fields=(R.FieldSpec("title"), R.FieldSpec("as_agent")),
    )
    with pytest.raises(ValueError, match="as_agent"):
        S.record_commands(bad)


# -- the CLI, end to end ---------------------------------------------------------------------


def test_add_list_show_edit_search_remove_revise_through_the_cli(cli):
    code, out, _ = cli(
        "note", "add", "alpha", "--title", "Cache invalidation", "--body", "clear the cache"
    )
    assert (code, out.strip()) == (0, "added note alpha")
    cli(
        "note", "add", "beta", "--title", "Naming", "--tags", "style", "--tags", "words", "--pinned"
    )
    code, out, _ = cli("note", "list")
    assert out.splitlines() == ["alpha  [active] Cache invalidation", "beta  [active] Naming"]
    code, out, _ = cli("note", "list", "--where", "tags=style")
    assert out.splitlines() == ["beta  [active] Naming"]
    code, out, _ = cli("note", "show", "beta")
    assert "tags: ['style', 'words']" in out and "pinned: True" in out and "history:" in out
    assert (
        cli("note", "edit", "beta", "--title", "Naming things", "--no-pinned", "--weight", "3")[0]
        == 0
    )
    code, out, _ = cli("note", "edit", "beta", "--unset", "weight")
    assert (code, out.strip()) == (0, "edited note beta")
    code, out, _ = cli("note", "search", "clear the cache")
    assert out.splitlines()[0].startswith("alpha  [active]")
    assert cli("note", "remove", "alpha", "--reason", "obsolete")[0] == 0
    assert cli("note", "list")[1].splitlines() == ["beta  [active] Naming things"]
    assert len(cli("note", "list", "--all")[1].splitlines()) == 2
    code, out, _ = cli("note", "revise", "alpha", "--title", "Back", "--reason", "needed again")
    assert (code, out.strip()) == (0, "revised note alpha")


def test_json_is_the_whole_body_and_a_refusal_leads_with_its_reason(cli):
    cli("note", "add", "alpha", "--title", "t")
    code, out, _ = cli("note", "list", json_out=True)
    body = json.loads(out)
    assert code == 0 and body["total"] == 1 and body["rows"][0]["id"] == "alpha"
    code, out, _ = cli("note", "edit", "ghost", "--title", "x", json_out=True)
    body = json.loads(out)
    assert code == O.REFUSED and next(iter(body)) == "refusal"
    assert body["refusal"]["exit"] == O.REFUSED and "no note" in body["refusal"]["reason"]


def test_exit_codes_follow_the_outcome(cli):
    code, out, err = cli("note", "show", "ghost")
    assert (code, out) == (O.FAIL, "") and "ghost" in err
    code, out, err = cli("note", "list")
    assert (code, err) == (O.NOTHING, "") and "No note records" in out
    cli("note", "add", "alpha", "--title", "t")
    code, _out, err = cli("note", "add", "alpha", "--title", "t")
    assert code == O.REFUSED and "already exists" in err
    code, _out, err = cli("note", "list", "--where", "body=x")
    assert code == O.REFUSED and "filters are tags, level" in err
    code, _out, err = cli("note", "list", "--where", "oops")
    assert code == O.FAIL and "field=value" in err


def test_a_long_list_says_how_to_see_the_rest(cli):
    for i in range(3):
        cli("note", "add", f"n{i}", "--title", f"t{i}")
    out = cli("note", "list", "--limit", "2")[1]
    assert out.splitlines()[-1].startswith("(showing 2 of 3")


FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"


def test_the_duplicate_flags_are_the_shared_ones(cli, repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")  # conftest turns it off elsewhere
    assert run_cli(repo, "init")[0] == 0
    assert (
        run_cli(repo, "decision", "add", "--id", "D-old", "--title", FIRST, "--decision", FIRST)[0]
        == 0
    )
    ruled = dataclasses.replace(NOTE, name="ruled", def_kind="rule", aliases=())
    args = ("ruled", "add", "r-new", "--title", SECOND, "--body", SECOND)
    code, _out, err = cli(*args, kind=ruled)
    assert code == O.REFUSED and "D-old" in err and "--extends" in err
    code, out, err = cli(*args, "--check", kind=ruled)
    assert code == 0 and "D-old" in out
    assert R.record_list(repo, ruled).exit == O.NOTHING  # --check wrote nothing
    code, out, _ = cli(*args, "--new", kind=ruled)
    assert (code, out.strip()) == (0, "added ruled r-new")


def test_more_than_one_answer_flag_is_refused(cli):
    code, _out, err = cli("note", "add", "a", "--title", "t", "--new", "--extends", "x")
    assert code == O.FAIL and "not several" in err


# -- the tool ---------------------------------------------------------------------------------


def test_the_tool_entry_dispatches_every_verb(repo):
    entry = _tool().tool_entry()
    call = entry["api"]
    assert call(repo, {"verb": "add", "id": "a", "title": "t", "tags": ["x"]}, "ag").exit == O.OK
    rows = call(repo, {"verb": "list", "where": ["tags=x"]}, "ag").data["rows"]
    assert [r["id"] for r in rows] == ["a"]
    assert call(repo, {"verb": "show", "id": "a"}, "ag").data["fields"]["title"] == "t"
    assert (
        call(repo, {"verb": "edit", "id": "a", "title": "t2", "unset": ["tags"]}, "ag").exit == O.OK
    )
    assert "tags" not in call(repo, {"verb": "show", "id": "a"}, "ag").data["fields"]
    assert call(repo, {"verb": "search", "query": "t2", "mode": "exact"}, "ag").data["total"] == 1
    assert (
        call(repo, {"verb": "revise", "id": "a", "title": "t3", "reason": "r"}, "ag").exit == O.OK
    )
    assert call(repo, {"verb": "remove", "id": "a", "reason": "done"}, "ag").exit == O.OK


def test_the_tool_answers_the_duplicate_check_with_relation_and_check_only(repo):
    call = _tool().tool_entry()["api"]
    dry = call(repo, {"verb": "add", "id": "a", "title": "t", "check_only": True}, "ag")
    assert dry.exit in (O.OK, O.NOTHING) and not R.record_list(repo, NOTE).data.get("rows")
    assert (
        call(repo, {"verb": "add", "id": "a", "title": "t", "relation": "new"}, "ag").exit == O.OK
    )


def test_a_malformed_call_raises_the_error_every_tool_does(repo):
    call = _tool().tool_entry()["api"]
    for args in (
        {},
        {"verb": "nope"},
        {"verb": "show"},
        {"verb": "search"},
        {"verb": "list", "where": ["no-equals"]},
    ):
        with pytest.raises(ValueError):
            call(repo, args, "ag")


def test_an_argument_the_verb_does_not_take_is_refused_by_name(repo):
    call = _tool().tool_entry()["api"]
    out = call(repo, {"verb": "list", "reason": "why", "title": "t"}, "ag")
    assert out.exit == O.REFUSED and "does not take reason, title" in out.reason
    assert (
        call(repo, {"verb": "list", "limit": 0, "all": False, "where": []}, "ag").exit == O.NOTHING
    )


# -- aliases -----------------------------------------------------------------------------------


def test_the_old_group_word_still_works_and_says_so():
    p = _parser()
    a = p.parse_args(["notes", "list"])
    assert a.fn is p.parse_args(["note", "list"]).fn  # the alias reaches the same handler
    used = used_aliases(p, a, ["notes", "list"])
    assert [u.old for u in used] == ["notes"] and used[0].new == "note"
    assert S.record_group_aliases(R.KINDS["rule"], "0.1.17")["rule"][0].old == "rules"
    assert (
        S.record_group_aliases(
            R.RecordKind(name="y", def_kind="skill", summary="", fields=(R.FieldSpec("title"),)),
            "0.1.17",
        )
        == {}
    )
