"""`record.corrected`: a logical correction of any record kind, with an audit entry that never
holds the old value (REC-correct-event, D-record-correction-layers-2)."""

from __future__ import annotations

import json

import pytest

from ddflow import api
from ddflow.core import digest as D
from ddflow.core.events import Event
from ddflow.core.model import fold

LEAKED = "leaked value zebra lantern 4711"


def _st(repo):
    return api.items._load(repo, "agent-test")[2]


def _correct(log, rid, kind, fields, reason="a typo", **extra):
    data = {
        "kind": kind,
        "fields": fields,
        "reason": reason,
        "digest_chars": 12,
        "min_chars": 8,
        **extra,
    }
    return log.append("record.corrected", rid, data)


@pytest.fixture
def seeded(repo, log):
    log.append("bug.found", "B1", {"summary": f"login fails with {LEAKED}", "title": "breaks"})
    log.append("lesson.recorded", "L1", {"title": "t", "rule": f"use {LEAKED}", "why": "w"})
    log.append(
        "research.recorded",
        "R1",
        {"question": "q?", "claim": f"claim {LEAKED}", "verdict": "THEORETICAL"},
    )
    log.append("decision.recorded", "D1", {"title": "dt", "decision": f"chose {LEAKED}"})
    log.append("memory.recorded", "M1", {"text": f"fact {LEAKED}"})
    log.append("task.added", "T1", {"title": "t", "body": f"do {LEAKED}"})
    return repo


@pytest.mark.parametrize(
    ("rid", "kind", "field", "book", "attr"),
    [
        ("B1", "bug", "summary", "bugs", "summary"),
        ("L1", "lesson", "rule", "lessons", "rule"),
        ("R1", "research", "claim", "research", "claim"),
        ("D1", "decision", "decision", "decisions", "decision"),
        ("M1", "memory", "text", "memories", "text"),
        ("T1", "task", "body", "items", "body"),
    ],
)
def test_the_corrected_text_is_what_the_state_holds(seeded, log, rid, kind, field, book, attr):
    _correct(log, rid, kind, {field: "the clean text"})
    rec = getattr(_st(seeded), book)[rid]
    assert getattr(rec, attr) == "the clean text"


@pytest.mark.parametrize(
    ("rid", "kind", "field", "book"),
    [
        ("B1", "bug", "summary", "bugs"),
        ("L1", "lesson", "rule", "lessons"),
        ("R1", "research", "claim", "research"),
        ("D1", "decision", "decision", "decisions"),
        ("M1", "memory", "text", "memories"),
        ("T1", "task", "body", "items"),
    ],
)
def test_the_audit_entry_names_who_why_and_digests_never_the_old_value(
    seeded, log, rid, kind, field, book
):
    old = getattr(getattr(_st(seeded), book)[rid], field)
    _correct(log, rid, kind, {field: "the clean text"}, reason="removed a credential")
    entry = _st(seeded).corrections[f"{kind}:{rid}"][-1]
    assert entry["by"] == "agent-test"
    assert entry["reason"] == "removed a credential"
    (row,) = entry["fields"]
    assert row["field"] == field
    assert row["old"] == D.content_digest(D.canonical(old), length=12)
    assert row["new"] == D.content_digest(D.canonical("the clean text"), length=12)
    assert LEAKED not in json.dumps(entry)


def test_the_event_line_never_carries_the_old_value(seeded, log):
    _correct(log, "B1", "bug", {"summary": "clean"})
    lines = [
        line
        for p in (seeded / ".ddflow" / "events").glob("*.jsonl")
        for line in p.read_text().splitlines()
        if "record.corrected" in line
    ]
    assert lines
    assert all(LEAKED not in line for line in lines)


def test_a_short_old_value_gets_no_digest(repo, log):
    log.append("bug.found", "B2", {"summary": "pw1", "title": "t"})
    _correct(log, "B2", "bug", {"summary": "a long enough clean text"}, min_chars=8)
    (row,) = _st(repo).corrections["bug:B2"][-1]["fields"]
    assert row["old"] == ""
    assert row["new"]


def test_two_corrections_keep_two_entries_in_order(seeded, log):
    _correct(log, "B1", "bug", {"summary": "one"}, reason="first")
    _correct(log, "B1", "bug", {"summary": "two"}, reason="second")
    bug = _st(seeded).bugs["B1"]
    assert bug.summary == "two"
    assert [c["reason"] for c in _st(seeded).corrections["bug:B1"]] == ["first", "second"]


def test_replay_gives_the_same_state(seeded, log):
    _correct(log, "B1", "bug", {"summary": "one"})
    events = log.read_all()
    assert fold(events).bugs["B1"] == fold(list(events)).bugs["B1"]


def _ev(data, subject="B1"):
    return Event(kind="record.corrected", subject=subject, data=data, agent="a", lamport=99)


def _folded(seeded, log, data, subject="B1"):
    return fold([*log.read_all(), _ev(data, subject)], strict=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "fields": {"no_such_field": "x"}},
        lambda d: {**d, "fields": "not an object"},
        lambda d: {**d, "fields": {}},
        lambda d: {**d, "reason": ""},
        lambda d: {k: v for k, v in d.items() if k != "reason"},
        lambda d: {**d, "kind": "nonsense"},
        lambda d: {**d, "kind": "rule"},
        lambda d: {k: v for k, v in d.items() if k != "digest_chars"},
        lambda d: {**d, "digest_chars": 0},
        lambda d: {**d, "digest_chars": 10_000},
        lambda d: {**d, "min_chars": "eight"},
    ],
)
def test_a_malformed_correction_fails_the_strict_fold(seeded, log, mutate):
    good = {
        "kind": "bug",
        "fields": {"summary": "x"},
        "reason": "r",
        "digest_chars": 12,
        "min_chars": 8,
    }
    with pytest.raises(ValueError, match=r"record\.corrected"):
        _folded(seeded, log, mutate(good))


def test_correcting_a_record_that_does_not_exist_fails_the_strict_fold(seeded, log):
    good = {
        "kind": "bug",
        "fields": {"summary": "x"},
        "reason": "r",
        "digest_chars": 12,
        "min_chars": 8,
    }
    with pytest.raises(ValueError, match=r"record\.corrected.*B404"):
        _folded(seeded, log, good, subject="B404")


def test_a_kind_that_does_not_match_the_record_fails(seeded, log):
    good = {
        "kind": "lesson",
        "fields": {"rule": "x"},
        "reason": "r",
        "digest_chars": 12,
        "min_chars": 8,
    }
    with pytest.raises(ValueError, match=r"record\.corrected"):
        _folded(seeded, log, good, subject="B1")


def test_a_field_the_kind_lacks_is_refused_not_dropped(seeded, log):
    good = {
        "kind": "memory",
        "fields": {"severity": "high"},
        "reason": "r",
        "digest_chars": 12,
        "min_chars": 8,
    }
    with pytest.raises(ValueError, match="severity"):
        _folded(seeded, log, good, subject="M1")


def test_history_names_a_correction():
    from ddflow.surfaces.history_verbs import HISTORY_VERBS

    assert HISTORY_VERBS.get("record.corrected") == "corrected"


def test_the_kind_is_declared_with_its_text_fields_redacted():
    from ddflow.core import eventkinds as EK

    k = EK.EVENT_KINDS.get("record.corrected")
    assert k is not None
    assert "reason" in k.text
    assert "fields" in k.text
