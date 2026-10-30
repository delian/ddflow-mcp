"""`ddflow waves`: dependency waves, `--stamp`, `--check` and stamping by default (Bf03dc5a256).

Replaces the stub test (`ddflow waves --stamp` used to exit 2 saying it was not built, bug
B47acb25115). D-wave-semantics: wave 0 = no open prerequisite (task-level or inherited from
a phase); wave N = 1 + the highest open prerequisite wave; only changed items are written.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli, write_config
from helpers import items_of

from ddflow.infra.log import EventLog


@pytest.fixture(autouse=True)
def _default_stamping(monkeypatch):
    """The suite turns stamping off (conftest); this file runs the shipped default."""
    monkeypatch.delenv("DDFLOW_WAVES_AUTO_STAMP")


def _waves(repo) -> dict[str, str]:
    return {
        i: t
        for i, it in items_of(repo).items()
        for t in it.tags
        if t.startswith("wave:") and not it.terminal
    }


def _wave_tags(repo, iid) -> set[str]:
    """The exact set of `wave:*` tags on one item: a hand edit could leave a stale one, so
    the answer is a set, not the last tag (which would hide `wave:9` beside `wave:2`)."""
    return {t for t in items_of(repo)[iid].tags if t.startswith("wave:")}


def _tag_events(repo) -> int:
    return sum(1 for e in EventLog(repo, "probe").read_all() if e.kind == "task.updated")


def _chain(repo):
    """A (free) <- B <- C, plus D under phase P that needs A: D inherits nothing, E is free."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A")
    run_cli(repo, "task", "add", "C", "--globs", "c.py", "--needs", "B")
    run_cli(repo, "task", "add", "E", "--globs", "e.py")


def _manual(repo):
    run_cli(repo, "init")
    assert not write_config(repo, [("waves.auto_stamp", False)])[0]


def test_waves_prints_the_levels(repo):
    _manual(repo)
    _chain(repo)
    code, out, err = run_cli(repo, "waves", "--json")
    assert code == 0, err
    got = json.loads(out)["waves"]
    assert got["0"] == ["A", "E"] and got["1"] == ["B"] and got["2"] == ["C"]


def test_check_is_stale_until_stamp_and_stamp_is_idempotent(repo):
    _manual(repo)
    _chain(repo)
    assert run_cli(repo, "waves", "--check")[0] == 1
    assert run_cli(repo, "waves", "--stamp")[0] == 0
    assert _waves(repo) == {"A": "wave:0", "B": "wave:1", "C": "wave:2", "E": "wave:0"}
    assert run_cli(repo, "waves", "--check")[0] == 0
    before = _tag_events(repo)
    assert run_cli(repo, "waves", "--stamp")[0] == 0
    assert _tag_events(repo) == before


def test_a_hand_edit_is_overwritten_and_only_that_item_is_written(repo):
    _manual(repo)
    _chain(repo)
    run_cli(repo, "waves", "--stamp")
    run_cli(repo, "update", "C", "--add-tags", "wave:9", "--remove-tags", "wave:2")
    before = _tag_events(repo)
    assert run_cli(repo, "waves", "--check")[0] == 1
    run_cli(repo, "waves", "--stamp")
    assert _wave_tags(repo, "C") == {"wave:2"}, _wave_tags(repo, "C")
    assert _tag_events(repo) == before + 1


def test_a_phase_level_need_is_inherited(repo):
    _manual(repo)
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1")
    run_cli(repo, "phase", "add", "P2", "--needs", "P1")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "t1.py")
    run_cli(repo, "task", "add", "T2", "--phase", "P2", "--globs", "t2.py")
    got = json.loads(run_cli(repo, "waves", "--json")[1])["waves"]
    assert got["0"] == ["T1"] and got["1"] == ["T2"]


def test_stamping_is_the_default_and_incremental(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--globs", "b.py", "--needs", "A")
    assert _waves(repo) == {"A": "wave:0", "B": "wave:1"}
    run_cli(repo, "task", "add", "C", "--globs", "c.py", "--needs", "B")
    assert _waves(repo)["C"] == "wave:2"
    before = _tag_events(repo)
    run_cli(repo, "abandon", "A", "--reason", "x")  # B becomes free, C moves with it
    assert _waves(repo) == {"B": "wave:0", "C": "wave:1"}
    assert _tag_events(repo) == before + 2  # B and C only


def test_the_knob_turns_default_stamping_off(repo):
    _manual(repo)
    _chain(repo)
    assert _waves(repo) == {}
