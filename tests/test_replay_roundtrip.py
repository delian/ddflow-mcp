"""D-no-deletion: replaying the log from scratch gives the live folded state, and no record or
event id disappears across close, abandon, fold, supersede and forget (DX-replay-roundtrip)."""

from __future__ import annotations

import itertools
import json

import pytest
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import replay_roundtrip as rt


def _ok(repo, *argv):
    code, out, err = run_cli(repo, *argv)
    assert code == 0, f"{argv}: {out}{err}"
    return out


def _log(repo) -> EventLog:
    return EventLog(repo, "tester")


@pytest.fixture
def history(repo):
    """A log that has seen every way of ending a record, with the event ids after each step."""
    _ok(repo, "init")
    # `init` writes no event, so there is nothing of it to lose: the first snapshot is after
    # the first add.
    snapshots: list[tuple[str, set[str]]] = []

    def step(label, *argv):
        _ok(repo, *argv)
        snapshots.append((label, {e.id for e in _log(repo).read_all()}))

    for t in ("A", "B", "C", "D"):
        step(f"add {t}", "task", "add", t, "--globs", f"{t}.py", "--body", f"body {t}")
    step("abandon", "abandon", "C", "--reason", "not needed", "--force")
    step("fold", "task", "fold", "B", "A", "--reason", "same work")
    step("remove", "remove", "D", "--reason", "mistaken")
    for d in ("D1", "D2"):
        step(
            f"decision {d}",
            "decision",
            "add",
            "--new",
            "--id",
            d,
            "--title",
            f"decision {d}",
            "--decision",
            f"decided {d}",
        )
    step("supersede", "decision", "supersede", "D1", "--by", "D2")
    step("memory", "memory", "add", "--id", "M1", "a machine fact")
    step("forget", "memory", "forget", "M1", "--reason", "stale")
    step("bug", "bug", "found", "--id", "BUG1", "--summary", "it broke", "--no-task")
    step("close", "bug", "invalid", "BUG1", "--reason", "not a bug")
    return repo, snapshots


def test_rebuilt_state_equals_live_state_and_nothing_is_lost(history):
    repo, snapshots = history
    log = _log(repo)
    events = log.read_all()
    live = fold(events, strict=False)
    assert rt.check(events, live, checkpoints=len(events)) == []
    # Every record the history ended is still there, in its ended state.
    assert {"A", "B", "C", "D"} <= set(live.items)
    assert live.items["C"].state == "abandoned" and live.items["B"].state == "abandoned"
    assert live.decisions["D1"].status == "superseded" and "D2" in live.decisions
    assert live.memories["M1"].forgotten and "BUG1" in live.bugs
    # No event id of an earlier step is gone from a later one.
    for (_, earlier), (label, later) in itertools.pairwise(snapshots):
        assert earlier <= later, label


def test_a_fold_that_drops_a_record_is_caught(history, monkeypatch):
    repo, _ = history
    events = _log(repo).read_all()
    live = fold(events, strict=False)
    real = rt.fold

    def dropping(evs, **kw):
        st = real(evs, **kw)
        if len(evs) == len(events):
            st.decisions.pop("D1", None)  # the mutation: a superseded decision is deleted
        return st

    monkeypatch.setattr(rt, "fold", dropping)
    problems = rt.check(events, live, checkpoints=len(events))
    assert any("decisions" in p for p in problems)
    assert any("decision D1 was in the state" in p for p in problems)


def test_a_missing_event_changes_the_rebuilt_state(history):
    repo, _ = history
    events = _log(repo).read_all()
    live = fold(events, strict=False)
    dropped = [e for e in events if not (e.kind == "memory.forgotten")]
    assert len(dropped) < len(events)
    assert "memories" in " ".join(rt.check(dropped, live))


def test_a_duplicated_event_id_is_reported(history):
    repo, _ = history
    events = _log(repo).read_all()
    live = fold(events, strict=False)
    assert any("occurs twice" in p for p in rt.check([*events, events[0]], live))


def test_replay_verify_runs_the_round_trip(history):
    repo, _ = history
    code, out, err = run_cli(repo, "--json", "replay", "--verify")
    assert code == 0, err
    assert json.loads(out)["roundtrip"] == []
