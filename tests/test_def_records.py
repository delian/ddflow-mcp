"""B-uni-def-records: one managed-definition record for every kind of definition.

The five `def.*` events, their fold into `State.defs`, the one write path (`api.defs`)
with its refusals, and the add-time duplicate check a new definition runs.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import defs as A
from ddflow.core import defs as D
from ddflow.core.events import PROVENANCE_KINDS, Event, canonical, content_digest
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog

SPEC = {"title": "Nightly sweep", "every_days": 1, "prompt": "sweep"}


def _state(repo: Path):
    return fold(EventLog(repo, "a").read_all())  # strict: every kind written is known


def _ev(kind: str, subject: str, data: dict, lamport: int, ts: str = "t") -> Event:
    return Event(kind=kind, subject=subject, data=data, agent="x", lamport=lamport, ts=ts)


# -- the vocabulary -------------------------------------------------------------------------


def test_the_five_kinds_are_folded_and_kept_as_provenance():
    assert set(D.EVENT_KINDS) <= set(HANDLERS)
    assert set(D.EVENT_KINDS) <= PROVENANCE_KINDS


def test_event_ids_are_unchanged_by_the_shared_digest():
    """`compute_id` now goes through `content_digest`; every id already in a log must
    still be the id its content hashes to."""
    ev = _ev("task.added", "T1", {"title": "x"}, 7, ts="2026-01-01T00:00:00Z")
    want = "e" + hashlib.blake2b(canonical(ev.body()).encode(), digest_size=12).hexdigest()
    assert ev.compute_id() == want
    assert D.digest({"a": 1}) == content_digest({"a": 1}, size=16)
    assert D.digest({"a": 1, "b": 2}) == D.digest({"b": 2, "a": 1})


# -- record, update, retire, supersede, merge ------------------------------------------------


def test_a_definition_is_recorded_with_its_envelope_and_history(repo):
    out = A.def_record(
        repo, "schedule", "nightly", SPEC, source=".ddflow/schedules/nightly.toml", agent="a"
    )
    assert out.exit == 0, out.reason
    assert out.data["replaced"] is False and out.data["digest"] == D.digest(SPEC)
    rec = _state(repo).defs["schedule:nightly"]
    assert (rec.kind, rec.id, rec.status, rec.fields) == ("schedule", "nightly", "active", SPEC)
    assert rec.source == ".ddflow/schedules/nightly.toml"
    assert rec.provenance["by"] == "a" and rec.by == "a"
    assert [h["event"] for h in rec.history] == ["def.recorded"]


def test_recording_again_replaces_the_fields_and_keeps_when_it_began(repo):
    A.def_record(repo, "skill", "review", {"a": 1, "b": 2}, agent="a")
    first = _state(repo).defs["skill:review"]
    out = A.def_record(repo, "skill", "review", {"a": 9}, agent="b")
    assert out.exit == 0 and out.data["replaced"] is True
    rec = _state(repo).defs["skill:review"]
    assert rec.fields == {"a": 9}  # replaced, not merged
    assert (rec.at, rec.by) == (first.at, "a")
    assert len(rec.history) == 2


def test_an_update_changes_only_what_it_names_and_none_removes(repo):
    A.def_record(repo, "agent", "critic", {"model": "m1", "tools": "read", "temp": 1}, agent="a")
    out = A.def_update(repo, "agent", "critic", {"model": "m2", "temp": None}, agent="a")
    assert out.exit == 0 and out.data["changed"] == ["model", "temp"]
    rec = _state(repo).defs["agent:critic"]
    assert rec.fields == {"model": "m2", "tools": "read"}
    assert rec.digest == D.digest(rec.fields)


def test_null_means_remove_in_an_update_and_is_refused_in_a_whole_definition(repo):
    out = A.def_record(repo, "skill", "s", {"a": None, "b": 1}, agent="a")
    assert out.exit == 1 and "null" in out.reason
    A.def_record(repo, "skill", "s", {"a": 0, "b": 1}, agent="a")
    # a field the update does not name is kept, falsy or not
    assert A.def_update(repo, "skill", "s", {"b": 2}, agent="a").exit == 0
    assert _state(repo).defs["skill:s"].fields == {"a": 0, "b": 2}


def test_a_provenance_only_update_is_written(repo):
    A.def_record(repo, "skill", "s", {"b": 1}, agent="a")
    out = A.def_update(repo, "skill", "s", {}, provenance={"reviewed": "yes"}, agent="a")
    assert out.exit == 0
    assert _state(repo).defs["skill:s"].provenance == {"by": "a", "reviewed": "yes"}


def test_an_update_that_changes_nothing_is_exit_2_and_writes_nothing(repo):
    A.def_record(repo, "doctype", "adr", {"sections": "context,decision"}, agent="a")
    before = len(EventLog(repo, "a").read_all())
    out = A.def_update(repo, "doctype", "adr", {"sections": "context,decision"}, agent="a")
    assert out.exit == 2
    assert len(EventLog(repo, "a").read_all()) == before


def test_retire_needs_a_reason_and_a_retired_definition_cannot_be_revised(repo):
    A.def_record(repo, "trigger", "ci-red", {"on": "ci.failed"}, agent="a")
    assert A.def_retire(repo, "trigger", "ci-red", reason=" ", agent="a").exit == 1
    assert A.def_retire(repo, "trigger", "ci-red", reason="replaced by hooks", agent="a").exit == 0
    rec = _state(repo).defs["trigger:ci-red"]
    assert (rec.status, rec.reason, rec.live) == ("retired", "replaced by hooks", False)
    again = A.def_update(repo, "trigger", "ci-red", {"on": "x"}, agent="a")
    assert again.exit == 3 and "retired" in again.reason
    # recording it again brings it back
    assert A.def_record(repo, "trigger", "ci-red", {"on": "y"}, agent="a").exit == 0
    assert _state(repo).defs["trigger:ci-red"].live


def test_supersede_names_the_successor_and_needs_both_active(repo):
    A.def_record(repo, "claim", "c1", {"claim": "x is fast"}, agent="a")
    A.def_record(repo, "claim", "c2", {"claim": "x is fast under load"}, agent="a")
    assert A.def_supersede(repo, "claim", "c1", "c1", agent="a").exit == 1  # itself
    assert A.def_supersede(repo, "claim", "c1", "nope", agent="a").exit == 3  # no successor
    out = A.def_supersede(repo, "claim", "c1", "c2", reason="narrower", agent="a")
    assert out.exit == 0 and out.data["successor"] == "c2"
    rec = _state(repo).defs["claim:c1"]
    assert (rec.status, rec.successor, rec.reason) == ("superseded", "c2", "narrower")
    # a superseded definition is not a successor
    A.def_record(repo, "claim", "c3", {"claim": "z"}, agent="a")
    assert A.def_supersede(repo, "claim", "c3", "c1", agent="a").exit == 3


def test_merge_records_what_the_successor_absorbed(repo):
    A.def_record(repo, "rule", "r1", {"content": "always run the suite in parallel"}, agent="a")
    A.def_record(repo, "rule", "r2", {"content": "tests run with xdist"}, agent="a")
    assert A.def_merge(repo, "rule", "r1", "r2", agent="a").exit == 0
    st = _state(repo)
    assert st.defs["rule:r1"].status == "merged" and st.defs["rule:r1"].successor == "r2"
    assert st.defs["rule:r2"].merged_from == ["r1"]


def test_one_name_in_two_kinds_is_two_definitions(repo):
    A.def_record(repo, "schedule", "nightly", SPEC, agent="a")
    A.def_record(repo, "skill", "nightly", {"body": "how to run the nightly"}, agent="a")
    assert {"schedule:nightly", "skill:nightly"} <= set(_state(repo).defs)
    listed = A.def_list(repo, kind="skill", agent="a")
    assert [r["id"] for r in listed.data["rows"]] == ["nightly"]


@pytest.mark.parametrize(
    "kind,rid,fields,why",
    [
        ("recipe", "x", {}, "unknown definition kind"),
        ("skill", "../x", {}, "definition id"),
        ("skill", "x", ["not", "an", "object"], "JSON object"),
        ("skill", "x", {"n": float("nan")}, "plain JSON"),
    ],
)
def test_a_bad_request_fails_and_writes_nothing(repo, kind, rid, fields, why):
    out = A.def_record(repo, kind, rid, fields, agent="a")
    assert out.exit == 1 and why in out.reason
    assert not [e for e in EventLog(repo, "a").read_all() if e.kind.startswith("def.")]


def test_revising_a_definition_nobody_recorded_is_refused(repo):
    assert A.def_update(repo, "skill", "ghost", {"a": 1}, agent="a").exit == 3
    assert A.def_retire(repo, "skill", "ghost", reason="r", agent="a").exit == 3
    assert A.def_show(repo, "skill", "ghost", agent="a").exit == 1


def test_show_carries_fields_provenance_and_history(repo):
    A.def_record(repo, "skill", "s", {"body": "b"}, provenance={"via": "import"}, agent="a")
    out = A.def_show(repo, "skill", "s", agent="a")
    assert out.exit == 0
    assert out.data["fields"] == {"body": "b"}
    assert out.data["provenance"] == {"by": "a", "via": "import"}
    assert out.data["history"][0]["event"] == "def.recorded"
    json.dumps(out.data)  # a wire body


# -- the fold ------------------------------------------------------------------------------


def test_an_update_before_its_definition_is_kept_and_the_definition_then_replaces_it():
    """Shards merge out of order: the fold never drops an event for arriving early."""
    env = {"kind": "skill", "id": "s"}
    evs = [
        _ev("def.updated", "skill:s", {**env, "fields": {"a": 1}}, 1),
        _ev("def.recorded", "skill:s", {**env, "fields": {"b": 2}}, 2),
    ]
    rec = fold(evs).defs["skill:s"]
    assert rec.fields == {"b": 2} and [h["event"] for h in rec.history] == [
        "def.updated",
        "def.recorded",
    ]


def test_a_merge_is_listed_by_its_successor_whichever_arrives_first():
    """Shards merge out of order: ``merged_from`` must not depend on whether the merge or
    the successor's definition was folded first. (A's own definition precedes its merge in
    every order: recorded AFTER a merge, a definition is brought back, by design.)"""
    a = _ev("def.recorded", "skill:A", {"kind": "skill", "id": "A", "fields": {}}, 1)
    merge = _ev("def.merged", "skill:A", {"kind": "skill", "id": "A", "successor": "B"}, 2)
    b = _ev("def.recorded", "skill:B", {"kind": "skill", "id": "B", "fields": {}}, 3)
    for order in ([a, b, merge], [a, merge, b], [b, a, merge]):
        st = fold(order)
        assert st.defs["skill:B"].merged_from == ["A"], [e.kind for e in order]
        assert st.defs["skill:A"].status == "merged"


# -- the add-time duplicate check -------------------------------------------------------------

FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"


@pytest.fixture
def checked(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")  # conftest turns it off elsewhere
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    code, out, err = run_cli(repo, "decision", "add", "--id", "D-old", "--title", FIRST,
                             "--decision", FIRST)  # fmt: skip
    assert code == 0, (code, out, err)
    return repo


def test_a_new_definition_that_repeats_a_record_is_refused_until_answered(checked):
    """A `rule` definition is checked by default ([dedupe].kinds holds `rule`)."""
    out = A.def_record(checked, "rule", "r-new", {"content": SECOND}, title=SECOND, agent="a")
    assert out.exit == 3, out.reason
    assert out.data["candidates"][0]["id"] == "D-old"
    assert not _state(checked).defs
    from ddflow.api._dedupe import Answer

    out = A.def_record(
        checked, "rule", "r-new", {"content": SECOND}, title=SECOND, answer=Answer("new"), agent="a"
    )
    assert out.exit == 0 and "rule:r-new" in _state(checked).defs


def test_recording_an_existing_definition_again_is_not_rechecked(checked):
    from ddflow.api._dedupe import Answer

    A.def_record(
        checked, "rule", "r", {"content": SECOND}, title=SECOND, answer=Answer("new"), agent="a"
    )
    again = A.def_record(checked, "rule", "r", {"content": SECOND + "!"}, title=SECOND, agent="a")
    assert again.exit == 0 and again.data["replaced"] is True


def test_a_kind_not_in_dedupe_kinds_is_not_checked(checked):
    out = A.def_record(checked, "skill", "s", {"body": SECOND}, title=SECOND, agent="a")
    assert out.exit == 0 and "candidates" not in out.data
