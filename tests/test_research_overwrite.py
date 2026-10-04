"""B14d796e03b: `research add` with a taken --id silently overwrote the record.

The add-time duplicate check returns early for an id that already exists, and the fold
replaces a research note wholesale, so a second add under the same id replaced the
question, claim and verdict with no trace in state. A re-add of a taken id is now
refused unless it is the same record again.
"""

from __future__ import annotations

from ddflow import api
from ddflow.core.model import fold
from ddflow.core.outcome import OK, REFUSED
from ddflow.infra.log import EventLog
from tests.conftest import run_cli


def _add(repo, **kw):
    kw.setdefault("verdict", "THEORETICAL")
    return api.research_add(repo, api.ResearchFinding(**kw))


def test_a_taken_research_id_with_different_text_is_refused(repo):
    run_cli(repo, "init")
    assert _add(repo, id="R1", question="first q", claim="first claim").exit == OK
    second = _add(repo, id="R1", question="a different question", claim="other claim")
    assert second.exit == REFUSED, second
    assert "R1" in second.reason and "--extends R1" in second.reason
    r1 = fold(EventLog(repo, "reader").read_all()).research["R1"]
    assert (r1.question, r1.claim) == ("first q", "first claim")


def test_a_changed_verdict_under_a_taken_id_is_refused_too(repo):
    run_cli(repo, "init")
    assert _add(repo, id="R1", question="q one", claim="c").exit == OK
    out = _add(repo, id="R1", question="q one", claim="c", verdict="REFUTED", probe="p")
    assert out.exit == REFUSED, out


def test_the_same_record_again_is_idempotent(repo):
    run_cli(repo, "init")
    assert _add(repo, id="R1", question="q one", claim="c").exit == OK
    again = _add(repo, id="R1", question="q one", claim="c")
    assert again.exit == OK, again


def test_the_cli_exits_3_and_research_list_keeps_the_first(repo):
    run_cli(repo, "init")
    rc, _, _ = run_cli(
        repo, "research", "add", "--id", "R1", "--question", "first q", "--verdict", "THEORETICAL"
    )
    assert rc == 0
    rc, out, err = run_cli(
        repo, "research", "add", "--id", "R1", "--question", "second q", "--verdict", "THEORETICAL"
    )
    assert rc == 3, (out, err)
    _, listing, _ = run_cli(repo, "research", "list")
    assert "first q" in listing and "second q" not in listing
