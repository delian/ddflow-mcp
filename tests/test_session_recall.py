"""Session summaries are searchable: `session end --summary` is folded, indexed and
recalled, labelled as a summary (B194)."""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

PHRASE = "quarantined the flaky ledger reconciliation"


def _ended(repo, summary=PHRASE):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "--json", "session", "start")
    assert code == 0, err
    sid = json.loads(out)["session"]
    run_cli(repo, "session", "prompt", sid, "--text", "fix the ledger")
    assert run_cli(repo, "session", "end", sid, "--summary", summary)[0] == 0
    return sid


def test_recall_finds_a_phrase_only_the_summary_holds(repo):
    sid = _ended(repo)
    code, out, err = run_cli(repo, "--json", "recall", "flaky ledger reconciliation")
    assert code == 0, err
    hits = json.loads(out)["prompts"]
    summary = [h for h in hits if "SESSION SUMMARY" in h["headline"]]
    assert summary and PHRASE in summary[0]["body"], hits
    assert sid in summary[0]["headline"]


def test_the_summary_is_folded_and_a_bare_end_does_not_blank_it(repo):
    sid = _ended(repo)
    EventLog(repo).append("session.ended", sid, {})
    assert fold(EventLog(repo).read_all(), strict=False).sessions[sid].summary == PHRASE


def test_the_human_recall_labels_it_a_summary_not_the_operators_words(repo):
    _ended(repo)
    out = run_cli(repo, "recall", "flaky ledger reconciliation")[1]
    assert "SESSION SUMMARY" in out and "operator asked: " + PHRASE not in out


def test_session_show_carries_the_summary(repo):
    sid = _ended(repo)
    assert PHRASE in run_cli(repo, "session", "show", sid)[1]
