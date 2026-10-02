"""A bug carries an optional title, severity and scope, and a `bug.reported_upstream` event
records where an upstream report went (B-bug-scope-event, decisions D-upstream-reporting
and D-export).  Old events fold unchanged; an old ddflow ignores the new fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_add_dedupe_mcp import call

from ddflow.api import knowledge as K
from ddflow.core.events import Event
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import cli
from ddflow.surfaces.mcp import TOOLS


def _bugs(repo):
    return fold(EventLog(repo, "a").read_all(), strict=False).bugs


def test_bug_found_records_title_severity_and_scope(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "--json", "bug", "found", "--summary", "claim drops the lease on crash",
        "--title", "Lease dropped", "--severity", "high", "--scope", "ddflow",
    )  # fmt: skip
    assert code == 0, err
    b = _bugs(repo)[json.loads(out)["id"]]
    assert (b.title, b.severity, b.scope) == ("Lease dropped", "high", "ddflow")


def test_scope_defaults_to_project_and_the_rest_is_optional(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "bug", "found", "--summary", "plain one")
    assert code == 0, err
    b = _bugs(repo)[json.loads(out)["id"]]
    assert (b.title, b.severity, b.scope) == ("", "", "project")


def test_an_unknown_scope_or_severity_is_refused_and_writes_nothing(repo):
    run_cli(repo, "init")
    for flag, value in (("--scope", "galaxy"), ("--severity", "meh")):
        code, _, err = run_cli(repo, "bug", "found", "--summary", "x y z", flag, value)
        assert code == 1 and value in err, err
    assert not _bugs(repo)


def test_an_event_without_the_new_fields_folds_unchanged():
    ev = Event(kind="bug.found", subject="B1", data={"summary": "old", "item": ""}, ts="t")
    b = fold([ev]).bugs["B1"]
    assert (b.title, b.severity, b.scope, b.upstream_url) == ("", "", "", "")


def test_reported_upstream_is_registered_and_folds_onto_the_bug():
    assert "bug.reported_upstream" in HANDLERS
    found = Event(kind="bug.found", subject="B1", data={"summary": "s", "scope": "ddflow"}, ts="t0")
    sent = Event(
        kind="bug.reported_upstream",
        subject="B1",
        data={"url": "https://x/1", "number": 7, "delivery": "issue", "sent_at": "t1", "digest": "d"},
        ts="t1",
    )
    b = fold([found, sent]).bugs["B1"]
    assert (b.upstream_url, b.upstream_number, b.upstream_delivery) == ("https://x/1", "7", "issue")
    assert (b.upstream_sent_at, b.upstream_digest) == ("t1", "d")


def test_the_title_takes_part_in_the_duplicate_match(repo):
    run_cli(repo, "init")
    run_cli(
        repo, "bug", "found", "--id", "B-a", "--summary", "it breaks", "--new",
        "--title", "worktree adoption refuses existing directory on claim",
    )  # fmt: skip
    code, out, _ = run_cli(
        repo, "--json", "similar", "claim refuses an existing worktree directory"
    )
    rows = json.loads(out)
    assert any(r["id"] == "B-a" for r in rows), rows
    hit = next(r for r in rows if r["id"] == "B-a")
    assert hit["title"].startswith("worktree adoption")


def test_similar_shows_severity_and_scope_of_a_bug(repo):
    run_cli(repo, "init")
    run_cli(
        repo, "bug", "found", "--id", "B-a", "--summary", "lease expiry silently drops claims",
        "--severity", "critical", "--scope", "ddflow",
    )  # fmt: skip
    _, out, _ = run_cli(repo, "similar", "lease expiry silently drops claims")
    assert "critical" in out and "ddflow" in out, out


def test_the_mcp_tool_takes_the_same_fields(repo):
    props = TOOLS["ddflow_bug_found"]["properties"]
    assert {"title", "severity", "scope"} <= set(props)
    run_cli(repo, "init")
    body, result = call(
        repo, "ddflow_bug_found", id="B-m", summary="mcp path", title="T", severity="low",
        scope="ddflow",
    )  # fmt: skip
    assert not result.get("isError"), body
    b = _bugs(repo)["B-m"]
    assert (b.title, b.severity, b.scope) == ("T", "low", "ddflow")
    body, result = call(repo, "ddflow_bug_found", id="B-n", summary="bad", scope="nope")
    assert result["_meta"]["exit"] == 1


def _parser_has_bug_report() -> bool:
    bug = next(
        a for a in cli.build_parser()._subparsers._group_actions[0].choices["bug"]._actions
        if hasattr(a, "choices") and a.choices
    )  # fmt: skip
    return "report" in bug.choices


def test_the_upstream_offer_is_made_only_while_the_command_exists(repo):
    # The offer names `ddflow bug report`: true only once that command is in the parser.
    assert K.BUG_REPORT_COMMAND == _parser_has_bug_report()
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "bug", "found", "--summary", "ddflow bug", "--scope", "ddflow")
    assert code == 0
    assert ("ddflow bug report" in out) == K.BUG_REPORT_COMMAND
    assert K.upstream_offer("project", "B1") == ""


def test_show_displays_title_severity_and_scope(repo):
    run_cli(repo, "init")
    run_cli(
        repo, "bug", "found", "--id", "B-s", "--summary", "body text", "--title", "Headline",
        "--severity", "medium", "--scope", "ddflow",
    )  # fmt: skip
    _, out, _ = run_cli(repo, "show", "B-s")
    assert "Headline" in out and "medium" in out and "ddflow" in out, out
