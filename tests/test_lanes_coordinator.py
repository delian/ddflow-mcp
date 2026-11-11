"""`ddflow lanes --coordinator`: the coordinator's one screen (CO-lanes-coordinator).

Running suites, lease holders (idle ones flagged), pending approvals, ready work per phase and
the blocked-by chain of a named item, over data ddflow already keeps; `ddflow lease` prints the
same holders. Every test fails against the code before the task.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from conftest import run_cli
from helpers import rpc_call, tool_json
from lanes_fixture import T0, Log, build_inputs, iso, multi_lane

from ddflow.config import Config
from ddflow.services import coordinator_view as CV
from ddflow.services import lanes as LN
from ddflow.services import suitelock as SL
from ddflow.surfaces.mcp import Server

NOW = T0 + 1901  # lane-stale has just gone stale; lane-work last beat 1301 s ago


def _seeded() -> Log:
    g = multi_lane()
    g.add(
        "ask.requested",
        "Q7",
        "lane-work",
        NOW - 600,
        type="approval",
        verb="merge",
        text="merge X into main",
        digest="d" * 64,
        expires_at=iso(NOW + 3600),
        canon="ddflow merge X",
    )
    return g


def _view(g: Log, *, item: str = "", suites=(), **knobs) -> dict:
    st, cfg, plan = build_inputs(g.events, NOW)
    for k, v in knobs.items():
        setattr(cfg.lanes, k, v)
    ledger = LN.build(g.events, st, cfg, NOW, plan)
    return CV.build(ledger, st, cfg, NOW, plan, suites=suites, item=item)


SUITE = {"holder": "lane-work", "pid": 4242, "at": NOW - 900, "slot": 0}


def test_a_seeded_state_renders_every_section():
    data = _view(_seeded(), item="W", suites=[SUITE], idle_s=1500)
    text = CV.render(data)
    for head in ("SUITES", "HOLDERS", "APPROVALS", "READY", "CHAIN W", "OWNERS"):
        assert head in text, head
    assert "pid 4242" in text and "lane-work" in text  # the running suite and its holder
    assert "Q7" in text and "merge" in text  # the pending approval with its command
    assert "lane-stale" in text and "Z" in text  # a holder and its item
    assert "W: deps" in text  # the first link of the blocked-by chain
    assert data["owners"]["lane-stale"] == "Z"


def test_an_idle_holder_is_flagged_and_a_busy_one_is_not():
    data = _view(_seeded(), idle_s=1500)
    by = {h["lane"]: h for h in data["holders"]}
    assert by["lane-stale"]["idle"] is True  # quiet 1801 s
    assert by["lane-work"]["idle"] is False  # quiet 1301 s
    assert "idle" in CV.render(data).split("lane-stale")[1].splitlines()[0]


def test_an_idle_s_of_zero_flags_nothing():
    data = _view(_seeded(), idle_s=0)
    assert not any(h["idle"] for h in data["holders"])


def test_ready_work_is_counted_per_phase():
    g = Log()
    g.add("phase.added", "PH", "lead", T0 - 2000, title="phase", needs=[], globs=[])
    for tid in ("A", "B"):
        g.add(
            "task.added", tid, "lead", T0 - 1000, title=tid, body="", globs=[f"{tid}.py"],
            needs=[], parent="PH",
        )  # fmt: skip
    data = _view(g)
    assert data["ready"]["PH"] == 2


def test_the_blocked_by_chain_names_who_holds_what():
    chain = _view(_seeded(), item="W")["chain"]
    assert chain[0]["item"] == "W" and chain[0]["reason"] == "deps"
    assert chain[1]["item"] == "Z" and "lane-stale" in chain[1]["detail"]


def test_output_is_bounded_with_a_partial_list_notice():
    data = _view(_seeded(), coordinator_max_rows=1)
    assert len(data["holders"]) == 1
    assert data["partial"]["holders"] == 1  # two holders, one shown
    assert "1 more holders not shown" in CV.render(data)


def test_suitelock_running_lists_every_held_slot(tmp_path, monkeypatch):
    Cfg = Config()
    Cfg.ci.max_concurrent_suites = 2

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with SL.hold(Cfg, "lane-a"):
        rows, queued = SL.running(Cfg)
    assert [r["holder"] for r in rows] == ["lane-a"] and rows[0]["pid"] > 0 and queued == 0
    assert SL.running(Cfg) == ([], 0)


@pytest.fixture
def busy(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--title", "X fixture", "--globs", "f.py")
    run_cli(repo, "task", "add", "Y", "--title", "Y fixture", "--globs", "f.py", "--needs", "X")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    return repo


def test_the_cli_renders_the_view_and_json_matches_the_mcp_tool(busy):
    code, out, _ = run_cli(busy, "lanes", "--coordinator", "--item", "Y", agent="reader")
    assert code == 0 and "HOLDERS" in out and "holder" in out and "CHAIN Y" in out
    _, js, _ = run_cli(busy, "lanes", "--coordinator", "--json")
    mcp = tool_json(rpc_call(Server(busy), "ddflow_lanes", coordinator=True))
    assert (
        json.loads(js)["coordinator"]["owners"] == mcp["coordinator"]["owners"] == {"holder": "X"}
    )


def test_ddflow_lease_lists_the_same_holders_as_lanes_coordinator(busy):
    code, out, _ = run_cli(busy, "lease", agent="reader")
    assert code == 0 and "holder" in out and " X " in out
    _, js, _ = run_cli(busy, "lease", "--json")
    _, cj, _ = run_cli(busy, "lanes", "--coordinator", "--json")
    names = lambda rows: [(r["lane"], r["item"]) for r in rows]  # noqa: E731
    assert names(json.loads(js)["holders"]) == names(json.loads(cj)["coordinator"]["holders"])


def test_a_cut_chain_owners_and_free_lanes_say_how_many_are_left():
    g = _seeded()
    g.task("T1", "t1.py", needs="W,Q,U")
    data = _view(g, item="T1", coordinator_max_rows=1)
    for section in ("chain", "owners"):
        assert data["partial"][section] >= 1, section
    assert "more chain not shown" in CV.render(data)


def test_a_suite_note_that_is_not_an_object_does_not_hide_the_other_suites(tmp_path, monkeypatch):
    Cfg = Config()
    Cfg.ci.max_concurrent_suites = 2
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with SL.hold(Cfg, "lane-a"):
        d = SL.state_dir()
        with SL.SLOTS.Slots(d, SL.NAME).hold(2, 0):  # the second slot, whose note is junk
            for i in range(2):
                if not (d / f"{SL.NAME}.{i}.holder").exists():
                    (d / f"{SL.NAME}.{i}.holder").write_text("null")
            rows, _ = SL.running(Cfg)
    assert [r["holder"] for r in rows] == ["lane-a"]


def test_the_positional_coordinator_view_takes_item_too(busy):
    code, out, _ = run_cli(busy, "lanes", "coordinator", "--item", "Y", agent="reader")
    assert code == 0 and "CHAIN Y" in out


def test_the_queued_count_ignores_an_injected_now(tmp_path):
    Cfg = Config()
    ticket = tmp_path / SL.QUEUE / "00000000000000000001-lane-x.ticket"
    ticket.parent.mkdir(parents=True)
    ticket.write_text("")
    assert (
        SL.running(Cfg, tmp_path, now=4_000_000_000.0)[1] == 1
    )  # a fresh ticket, whatever `now` says


def test_item_does_not_replace_the_lane_of_the_other_views(busy):
    code, out, _ = run_cli(busy, "lanes", "brief", "holder", "--item", "Y", agent="reader")
    assert code == 0 and out.startswith("# Lane brief: holder")


def _notes(tmp_path, monkeypatch, *bodies):
    """Hold slot 0 as lane-a, put each body of `bodies` in the notes of the next slots."""
    Cfg = Config()
    Cfg.ci.max_concurrent_suites = 1 + len(bodies)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with SL.hold(Cfg, "lane-a"):
        d = SL.state_dir()
        slots = SL.SLOTS.Slots(d, SL.NAME)
        with contextlib.ExitStack() as stack:
            for body in bodies:
                slot = stack.enter_context(slots.hold(1 + len(bodies), 0))
                (d / f"{SL.NAME}.{slot.index}.holder").write_text(body)
            return SL.running(Cfg, now=time.time() + 100)[0]


def test_a_note_with_a_string_time_is_read_as_a_number(tmp_path, monkeypatch):
    rows = _notes(tmp_path, monkeypatch, '{"holder": "b", "at": "5"}')
    by = {r["holder"]: r for r in rows}
    assert by["b"]["at"] == 5.0 and by["b"]["age_s"] > 0 and set(by) == {"b", "lane-a"}


@pytest.mark.parametrize(
    "junk",
    [
        '{"holder": "b", "at": "pending"}',
        '{"holder": "b", "at": null}',
        '{"holder": "b", "at": "nan"}',
        '{"holder": "b", "at": "inf"}',
    ],
)
def test_a_note_with_a_junk_time_is_skipped_and_the_rest_listed(tmp_path, monkeypatch, junk):
    assert [r["holder"] for r in _notes(tmp_path, monkeypatch, junk)] == ["lane-a"]


def test_a_note_with_no_time_is_listed_without_an_age(tmp_path, monkeypatch):
    rows = {r["holder"]: r for r in _notes(tmp_path, monkeypatch, '{"holder": "b"}')}
    assert "at" not in rows["b"] and rows["b"]["age_s"] == 0
