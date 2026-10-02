"""Links and additions are events of their own (decision D-no-duplicates, 2).

Add events carry link fields (`extends`, `duplicate_of`, `related`, `dedupe`); a later
addition is `record.extended` and a later link or a 'distinct' dismissal is `link.recorded`.
Both ACCUMULATE: two additions made at once -- two clones, two agents -- both survive in any
fold order, which a field on `task.updated` or `bug.found` could not offer (the last write
wins, and an addition to a bug overwrote its summary).
"""

from __future__ import annotations

import json
from itertools import permutations

import pytest
from conftest import run_cli

from ddflow.core import model
from ddflow.core.events import PROVENANCE_KINDS, Event
from ddflow.core.model import fold
from ddflow.services.sessions import replay


def _ev(kind: str, subject: str, lamport: int, agent: str = "x", **data) -> Event:
    e = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=f"t{lamport}")
    return Event(**{**e.__dict__, "id": e.compute_id()})


def _ext(lamport: int, agent: str, text: str, score: float = 0.7) -> Event:
    return _ev("record.extended", "B1", lamport, agent, text=text, who=agent, score=score)


BASE = [
    _ev("task.added", "T1", 1, title="the original"),
    _ev("bug.found", "B1", 2, item="T1", summary="the summary"),
]


def test_two_simultaneous_additions_both_survive_in_any_fold_order():
    adds = [
        _ext(10, "alice", "alice's words"),
        _ext(10, "bob", "bob's words"),
        _ext(11, "carol", "c"),
    ]
    seen = set()
    for order in permutations(adds):
        st = fold([*BASE, *order])
        texts = [a["text"] for a in st.links["B1"].extensions]
        assert sorted(texts) == ["alice's words", "bob's words", "c"]
        seen.add(tuple(texts))
    assert len(seen) == 1, "and they read in ONE order whatever order they folded in"


def test_an_addition_never_replaces_a_bugs_summary():
    st = fold([*BASE, _ext(10, "alice", "more detail")])
    assert st.bugs["B1"].summary == "the summary"
    st = fold([_ext(10, "alice", "more detail"), *BASE])
    assert st.bugs["B1"].summary == "the summary"
    assert st.links["B1"].extensions[0]["text"] == "more detail"


def test_an_addition_keeps_its_text_verbatim_and_its_provenance():
    text = "  line one\n\nline two with 'quotes'  "
    st = fold(
        [*BASE, _ev("record.extended", "B1", 10, "alice", text=text, who="alice", score=0.61)]
    )
    (a,) = st.links["B1"].extensions
    assert a["text"] == text and a["who"] == "alice" and a["score"] == 0.61
    assert a["at"] == "t10" and a["event"]


def test_the_same_event_delivered_twice_is_one_addition():
    e = _ext(10, "alice", "once")
    assert len(fold([*BASE, e, e]).links["B1"].extensions) == 1


def test_link_fields_on_add_events_are_recorded():
    st = fold(
        [
            _ev(
                "task.added",
                "T2",
                3,
                "alice",
                title="again",
                extends="T1",
                related=["T9", "T8"],
                dedupe={
                    "answer": "extends",
                    "score": 0.58,
                    "candidates": [{"id": "T1", "score": 0.58}],
                },
            ),
            _ev("bug.found", "B2", 4, "alice", summary="dup", duplicate_of="B1"),
        ]
    )
    rel = {(link["relation"], link["target"]) for link in st.links["T2"].links}
    assert rel == {("extends", "T1"), ("related", "T9"), ("related", "T8")}
    assert st.links["T2"].answer["answer"] == "extends"
    assert st.links["T2"].answer["candidates"] == [{"id": "T1", "score": 0.58}]
    assert [(x["relation"], x["target"]) for x in st.links["B2"].links] == [("duplicate_of", "B1")]
    assert "T1" not in st.links, "the record pointed AT gains nothing from an add"


def test_an_add_without_link_fields_leaves_no_trace():
    assert fold(BASE).links == {}


def test_later_links_and_distinct_dismissals_accumulate_in_any_order():
    a = _ev("link.recorded", "T1", 10, "alice", relation="related", target="T7", by="alice")
    b = _ev("link.recorded", "T1", 10, "bob", relation="distinct", target="T8", by="bob", score=0.5)
    c = _ev("link.recorded", "T1", 12, "bob", relation="duplicate_of", target="T9", by="bob")
    out = set()
    for order in permutations([a, b, c]):
        links = fold([*BASE, *order]).links["T1"].links
        out.add(json.dumps([(x["relation"], x["target"]) for x in links]))
    assert len(out) == 1
    got = json.loads(out.pop())
    assert sorted(got) == [["distinct", "T8"], ["duplicate_of", "T9"], ["related", "T7"]]


def test_distinct_is_a_dismissal_not_a_link():
    st = fold([*BASE, _ev("link.recorded", "T1", 10, relation="distinct", target="T8", by="a")])
    rec = st.links["T1"]
    assert rec.dismissed() == {"T8"}
    assert "T8" not in rec.linked()


def test_an_older_ddflow_reports_the_new_kinds_as_unknown(monkeypatch):
    events = [
        *BASE,
        _ext(10, "alice", "x"),
        _ext(11, "bob", "y"),
        _ev("link.recorded", "T1", 12, relation="related", target="T2", by="a"),
    ]
    monkeypatch.delitem(model.HANDLERS, "record.extended")
    monkeypatch.delitem(model.HANDLERS, "link.recorded")
    st = fold(events, strict=False)
    assert st.skipped_kinds == {"record.extended": 2, "link.recorded": 1}
    assert st.bugs["B1"].summary == "the summary", "everything else still folds"
    with pytest.raises(ValueError):
        fold(events)


def test_the_new_kinds_are_known_and_replayed():
    assert {"record.extended", "link.recorded"} <= model.known_kinds()
    assert {"record.extended", "link.recorded"} <= PROVENANCE_KINDS


def test_replay_rebuilds_additions_and_links():
    steps = replay(
        [
            _ev("task.added", "T2", 3, title="again", extends="T1"),
            _ext(10, "alice", "the verbatim addition"),
            _ev("link.recorded", "T1", 12, relation="distinct", target="T8", by="bob"),
        ]
    )
    text = "\n".join(s.text for s in steps)
    assert "extends T1" in text
    assert "the verbatim addition" in text and "alice" in text
    assert "T8" in text and "DISTINCT" in text


def test_the_log_accepts_the_new_kinds_and_replay_survives_a_real_log(repo):
    run_cli(repo, "init")
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "alice")
    log.append("task.added", "T1", {"title": "t"})
    log.append("record.extended", "T1", {"text": "added words", "who": "alice", "score": 0.7})
    log.append("link.recorded", "T1", {"relation": "related", "target": "T2", "by": "alice"})
    code, out, err = run_cli(repo, "replay")
    assert code == 0, err
    assert "added words" in out
    st = fold(EventLog(repo, "bob").read_all())
    assert st.links["T1"].extensions[0]["text"] == "added words"
