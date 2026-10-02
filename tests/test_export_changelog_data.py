"""The optional `changelog` field on item.completed and bug.fixed (decision D-export (4)):
`ddflow complete --changelog "Added: ..."`, `bug fixed --changelog`, the MCP twins; old events
fold unchanged and an older ddflow ignores the key."""

from __future__ import annotations

import json

import pytest
from conftest import finish, pass_pipeline, run_cli

from ddflow.core.events import CHANGELOG_CATEGORIES, parse_changelog
from ddflow.core.model import fold
from ddflow.infra.store import EventLog
from ddflow.surfaces.mcp import TOOLS, Server


def _mcp(repo, name, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return reply["result"]


def _state(repo):
    return fold(EventLog(repo, "reader").read_all())


def _task(repo, tid="T1"):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", tid, "--globs", "a.py")[0] == 0


def test_parse_accepts_category_text_case_insensitively():
    assert parse_changelog("Added: a thing") == {
        "category": "Added",
        "line": "a thing",
        "skip": False,
    }
    assert parse_changelog("  fixed :  x: y ") == {
        "category": "Fixed",
        "line": "x: y",
        "skip": False,
    }
    assert parse_changelog("SECURITY: z")["category"] == "Security"


@pytest.mark.parametrize("word", ["skip", "internal", " Skip ", "INTERNAL"])
def test_skip_and_internal_mark_an_entry_to_leave_out(word):
    assert parse_changelog(word) == {"category": "", "line": "", "skip": True}


@pytest.mark.parametrize("bad", ["", "   ", "no category here", "Feature: x", "Added:", "Added:  "])
def test_unknown_category_or_empty_line_is_refused_listing_the_categories(bad):
    with pytest.raises(ValueError) as e:
        parse_changelog(bad)
    for c in CHANGELOG_CATEGORIES:
        assert c in str(e.value)


def test_the_field_round_trips_through_fold_and_replay(repo):
    _task(repo)
    pass_pipeline(repo, "T1")
    code, _o, err = run_cli(
        repo, "complete", "T1", "--model", "claude-opus-5", "--changelog", "added: Export docs"
    )
    assert code == 0, err
    st = _state(repo)
    assert st.items["T1"].changelog == {"category": "Added", "line": "Export docs", "skip": False}
    ev = next(e for e in EventLog(repo, "r").read_all() if e.kind == "item.completed")
    assert ev.data["changelog"]["category"] == "Added"
    # a fold of the same events again (replay) is identical
    assert _state(repo).items["T1"].changelog == st.items["T1"].changelog


def test_complete_without_the_flag_never_prompts_and_folds_empty(repo):
    _task(repo)
    assert finish(repo, "T1")[0] == 0
    ev = next(e for e in EventLog(repo, "r").read_all() if e.kind == "item.completed")
    assert "changelog" not in ev.data
    assert _state(repo).items["T1"].changelog == {}


def test_changelog_skip_is_recorded(repo):
    _task(repo)
    assert finish(repo, "T1", "--changelog", "skip")[0] == 0
    assert _state(repo).items["T1"].changelog["skip"] is True


def test_a_bad_changelog_is_refused_before_anything_is_recorded(repo):
    _task(repo)
    pass_pipeline(repo, "T1")
    code, _o, err = run_cli(
        repo, "complete", "T1", "--model", "claude-opus-5", "--changelog", "Feature: x"
    )
    assert code != 0
    assert "Added" in err and "Security" in err
    assert _state(repo).items["T1"].state != "done"


def test_an_old_event_without_the_key_and_unknown_shapes_fold_unchanged(repo):
    _task(repo)
    log = EventLog(repo, "agent-x")
    # a hand-written or newer-shaped value must not break the fold of an older reader
    log.append("item.completed", "T1", {"sha": "", "kind": "task", "changelog": "garbage"})
    st = fold(log.read_all())
    assert st.items["T1"].state == "done"
    assert st.items["T1"].changelog == {}


def test_bug_fixed_takes_the_changelog_too(repo):
    _task(repo)
    log = EventLog(repo, "agent-x")
    log.append("bug.found", "B1", {"summary": "s"})
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    code, _o, err = run_cli(
        repo,
        "bug",
        "fixed",
        "B1",
        "--regression-test",
        "tests/test_x.py::test_x",
        "--changelog",
        "Security: closed a hole",
    )
    assert code == 0, err
    assert _state(repo).bugs["B1"].changelog == {
        "category": "Security",
        "line": "closed a hole",
        "skip": False,
    }
    code, _o, err = run_cli(
        repo,
        "bug",
        "fixed",
        "B1",
        "--regression-test",
        "tests/test_x.py::test_x",
        "--changelog",
        "nope",
    )
    assert code != 0


def test_mcp_complete_and_bug_fixed_carry_the_parameter(repo):
    for tool in ("ddflow_complete", "ddflow_bug_fixed"):
        prop = TOOLS[tool]["properties"]["changelog"]
        assert prop[0] == "string"
        assert len(prop[1]) <= 160  # the tools/list budget: a short description


def test_mcp_complete_records_the_changelog(repo):
    _task(repo)
    pass_pipeline(repo, "T1")
    res = _mcp(repo, "ddflow_complete", id="T1", model="claude-opus-5", changelog="Changed: x")
    assert res["_meta"]["exit"] == 0, res
    assert _state(repo).items["T1"].changelog["category"] == "Changed"
    bad = _mcp(repo, "ddflow_complete", id="T1", changelog="Zzz: x")
    assert bad["_meta"]["exit"] != 0
    assert json.loads(bad["content"][0]["text"])  # leads with the reason, parseable
