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


def test_the_idempotent_re_add_writes_nothing(repo):
    run_cli(repo, "init")
    _add(repo, id="R1", question="q one", claim="c")
    before = len(EventLog(repo, "reader").read_all())
    again = _add(repo, id="R1", question="q one", claim="c")
    assert again.data.get("unchanged") is True
    assert len(EventLog(repo, "reader").read_all()) == before


def test_an_id_held_by_another_kind_is_refused(repo):
    run_cli(repo, "init")
    rc, _, err = run_cli(repo, "phase", "add", "P1", "--title", "a phase")
    assert rc == 0, err
    out = _add(repo, id="P1", question="q", claim="c")
    assert out.exit == REFUSED and "phase" in out.reason, out


def test_every_recorded_field_is_a_note_attribute():
    """`_research_fields` is the payload AND the equality key: a field the fold does not
    hold would make every re-add read as changed (or, missing, as unchanged)."""
    from dataclasses import fields

    from ddflow.api.knowledge import _research_fields
    from ddflow.core.model import ResearchNote

    names = {f.name for f in fields(ResearchNote)}
    assert set(_research_fields(api.ResearchFinding(question="q", verdict="X"))) <= names


def test_the_mcp_tool_refuses_too(repo):
    import json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    _add(repo, id="R1", question="first q", claim="c")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_research_add",
                "arguments": {"id": "R1", "question": "second q", "verdict": "THEORETICAL"},
            },
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert reply["result"]["_meta"]["exit"] == REFUSED and "refusal" in body, body


def test_an_id_filed_after_the_state_was_read_is_still_refused(repo, monkeypatch):
    """The check is repeated under the log's lock: an agent that read the state before
    another filed R1 must not overwrite it (roborev 1436)."""
    from ddflow.api import knowledge as K

    run_cli(repo, "init")
    real = K._load

    def stale_load(*a, **k):
        log, cfg, _ = real(*a, **k)
        return log, cfg, fold([])

    _add(repo, id="R1", question="first q", claim="c")
    before = len(EventLog(repo, "reader").read_all())
    monkeypatch.setattr(K, "_load", stale_load)
    out = _add(repo, id="R1", question="second q", claim="other")
    assert out.exit == REFUSED, out
    assert len(EventLog(repo, "reader").read_all()) == before


def test_every_recorded_field_round_trips_through_the_fold(repo):
    """The idempotence compare reads the folded note: a field the fold dropped would make
    every re-add read as changed."""
    from ddflow.api.knowledge import _research_fields

    run_cli(repo, "init")
    f = api.ResearchFinding(
        id="R9",
        question="q",
        claim="c",
        mechanism="m",
        falsifier="f",
        probe="p",
        probe_output="o",
        verdict="CONFIRMED",
        sources="a,b",
        budget="1h",
        item="",
    )
    assert api.research_add(repo, f).exit == OK
    note = fold(EventLog(repo, "reader").read_all()).research["R9"]
    for k, v in _research_fields(f).items():
        assert getattr(note, k) == v, k
