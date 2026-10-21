"""B-bugs-phase-done-orphans: a done standing [bugs] phase must not orphan the fix task."""

from __future__ import annotations

from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog


def _done_bugs_phase(repo, cfg_phase="bugs"):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", cfg_phase, {"title": "Bugs"})
    log.append("item.completed", cfg_phase, {"sha": "abc"})
    assert state(repo).items[cfg_phase].terminal


def test_a_done_bugs_phase_does_not_orphan_the_fix_task(repo):
    _done_bugs_phase(repo)
    out = K.bug_found(repo, summary="s", id="Bx", agent="a")
    assert out.exit == 0, out.reason
    st = state(repo)
    fix = st.items["fix-Bx"]
    assert fix.parent and fix.parent != "bugs"
    parent = st.items[fix.parent]
    assert parent.kind == "phase" and not parent.terminal


def test_the_next_bug_reuses_the_fresh_bugs_phase(repo):
    _done_bugs_phase(repo)
    K.bug_found(repo, summary="s", id="Bx", agent="a")
    K.bug_found(repo, summary="t", id="By", agent="a")
    st = state(repo)
    assert st.items["fix-Bx"].parent == st.items["fix-By"].parent != ""
