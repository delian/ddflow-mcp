"""One set of progress counts (B-uni-tallies): a phase's done/total, per-state counts and
the board rows are computed once in core.progress and every renderer shows the same
numbers.

The rule, pinned here: a phase's tasks are its live tasks nested ANY depth below it,
sub-tasks and bug-fix tasks included; an abandoned task is out of the total and counted
on its own.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import run_cli
from helpers import state as _state

from ddflow import api
from ddflow.config import Config
from ddflow.core import progress as PR
from ddflow.services import progress_line as PL
from ddflow.services.export import kind_roadmap, registry
from ddflow.services.export import query as Q
from ddflow.views import markdown as M


def _items(*states: str) -> list:
    return [SimpleNamespace(state=s) for s in states]


@pytest.mark.parametrize(
    ("states", "want"),
    [
        ((), PR.Tally(0, 0, 0)),
        (("done",), PR.Tally(1, 1, 0)),
        (("done", "open"), PR.Tally(1, 2, 0)),
        # Abandoned is settled: out of the total, counted on its own.
        (("done", "abandoned"), PR.Tally(1, 1, 1)),
        (("abandoned",), PR.Tally(0, 0, 1)),
        # Every other state is outstanding work.
        (("running", "review", "blocked", "open", "done"), PR.Tally(1, 5, 0)),
    ],
)
def test_the_tally_rule(states, want):
    assert PR.tally(_items(*states)) == want


@pytest.mark.parametrize(
    ("tally", "complete"),
    [
        (PR.Tally(0, 0, 0), False),  # nothing to do is not "all done"
        (PR.Tally(0, 0, 2), False),
        (PR.Tally(2, 2, 1), True),
        (PR.Tally(1, 2, 0), False),
    ],
)
def test_complete_means_every_live_task_done_and_at_least_one(tally, complete):
    assert tally.complete is complete


def test_state_counts_lists_every_state_in_order_zeros_included():
    counts = PR.state_counts(_items("open", "done", "done", "abandoned"))
    assert list(counts) == list(PR.STATE_ORDER)
    assert counts == {
        "done": 2,
        "running": 0,
        "review": 0,
        "open": 1,
        "blocked": 0,
        "abandoned": 1,
    }


def _ok(repo, *argv):
    code, out, err = run_cli(repo, *argv)
    assert code == 0, f"{argv}: {out} {err}"
    return out


@pytest.fixture
def proj(repo):
    """P1: T1 done; T2 an umbrella over T2a (done) and T2b (open), T2b over T2b1 (open);
    T3 abandoned; a bug-fix task filed against T1. Live: T1 T2 T2a T2b T2b1 fix = 6,
    done 2, abandoned 1."""
    _ok(repo, "init")
    _ok(repo, "phase", "add", "P1", "--title", "p")
    for tid, extra in (
        ("T1", ["--phase", "P1"]),
        ("T2", ["--phase", "P1"]),
        ("T2a", ["--parent", "T2"]),
        ("T2b", ["--parent", "T2"]),
        ("T2b1", ["--parent", "T2b"]),
        ("T3", ["--phase", "P1"]),
    ):
        _ok(repo, "task", "add", tid, "--title", tid, "--globs", f"{tid}.py", *extra)
    _ok(repo, "complete", "T1", "--force", "--reason", "test setup")
    _ok(repo, "complete", "T2a", "--force", "--reason", "test setup")
    _ok(repo, "abandon", "T3", "--reason", "not needed")
    _ok(repo, "bug", "found", "--summary", "t one breaks", "--item", "T1", "--new")
    return repo


def test_phase_tally_counts_every_depth_and_fix_tasks_and_leaves_abandoned_out(proj):
    st = _state(proj)
    fixes = [t.id for t in st.tasks("P1") if t.fixes]
    assert len(fixes) == 1, "the fix task lands in T1's phase"
    assert PR.phase_tally(st, "P1") == PR.Tally(done=2, live=6, abandoned=1)


def test_every_renderer_shows_the_one_phase_tally(proj):
    st = _state(proj)
    cfg = Config.load(proj)
    assert "2/6 tasks · 1 abandoned" in M.board(st, cfg)
    rows = api.view_read(proj, "phase").data["rows"]
    assert {r["id"]: (r["done"], r["total"]) for r in rows}["P1"] == (2, 6)
    assert "Phase P1: 2/6 (33%)" in PL.report(st, cfg, "P1", mode="phase")
    lanes = kind_roadmap._data(Q.load(proj), registry.Filters())["lanes"]
    p1 = next(p for lane in lanes for p in lane["phases"] if p["id"] == "P1")
    assert (p1["done"], p1["total"]) == (2, 6)


def test_list_phase_leaves_abandoned_tasks_out_of_the_total(proj):
    """B01281f654e: `phase list` counted the abandoned T3 in P1's total (2/7)."""
    out = _ok(proj, "--json", "phase", "list")
    rows = json.loads(out)["rows"]
    assert {r["id"]: (r["done"], r["total"]) for r in rows}["P1"] == (2, 6)


def test_the_progress_line_counts_sub_tasks(proj):
    """Bb24939611d: the progress line counted P1's direct children only (1/3)."""
    st = _state(proj)
    assert PL.report(st, Config.load(proj), "T1", mode="phase").splitlines()[0] == (
        "Phase P1: 2/6 (33%)"
    )


def test_a_phase_with_an_open_sub_task_is_not_ready_to_close(proj):
    """Bb24939611d: with direct children only, P1 read closable while T2b1 was open."""
    for t in ("T2", "T2b"):
        _ok(proj, "complete", t, "--force", "--reason", "test setup")
    fix = next(t.id for t in _state(proj).tasks("P1") if t.fixes)
    _ok(proj, "complete", fix, "--force", "--reason", "test setup")
    st = _state(proj)
    assert "ready to close" not in PL.report(st, Config.load(proj), "T1", mode="on")
    _ok(proj, "complete", "T2b1", "--force", "--reason", "test setup")
    st = _state(proj)
    assert "1 ready to close" in PL.report(st, Config.load(proj), "T1", mode="on")


def test_completing_a_sub_task_names_its_phase(proj):
    """B45b55be6a4: a sub-task's progress line named its parent task as the phase."""
    st = _state(proj)
    out = PL.report(st, Config.load(proj), "T2b1", mode="phase")
    assert out.splitlines()[0] == "Phase P1: 2/6 (33%)"
    assert PR.phase_of(st, "T2b1") == PR.phase_of(st, "P1") == "P1"


def test_the_board_json_and_markdown_come_from_the_same_rows(proj):
    st = _state(proj)
    cfg = Config.load(proj)
    data = api.board(proj).data
    md = M.board(st, cfg)
    ids = [r["id"] for r in data["phases"][0]["tasks"]]
    fix = next(t.id for t in st.tasks("P1") if t.fixes)
    assert ids == ["T1", "T2", "T2a", "T2b", "T2b1", "T3", fix]  # a sub-task after its parent
    for r in data["phases"][0]["tasks"]:
        assert f"**{r['id']}**" in md
    secs = PR.board_rows(st, lambda t: ["a", "b"])
    assert [s.phase.id for s in secs if s.phase] == [p["id"] for p in data["phases"]]
    p1 = next(s for s in secs if s.phase and s.phase.id == "P1")
    assert p1.tally == PR.phase_tally(st, "P1")
    assert all(list(r["gates"]) == ["a", "b"] for r in p1.rows)
