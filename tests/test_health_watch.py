"""The watchdog finds stalled, locked, crashed and runaway lanes and corrects them
(DX-health-watch).

2026-10-15: an agent idled an hour behind a lease its heartbeat kept renewing, three agents
deadlocked 45 minutes, a stopped agent was found by reading timestamps. `core.health` judges
plain facts; `ddflow health --run` corrects per `[health].mode` and never kills a process or
deletes work."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import health
from ddflow.config import Config
from ddflow.core import clock
from ddflow.core import health as H
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import cadence as CAD
from ddflow.services import healthwatch as HW

A, B, C = "agent-a", "agent-b", "agent-c"
NOW = 1_000_000.0
LIM = H.Limits(stall_s=600, runaway_s=3600)


def _kinds(found):
    return sorted((f.kind, f.item) for f in found)


# -- the pure inspection ---------------------------------------------------------------


def test_a_live_lane_with_no_progress_is_stalled_and_an_active_one_never():
    idle = H.Lane("T1", A, True, NOW - 700)
    busy = H.Lane("T2", B, True, NOW - 30)
    found = H.inspect([idle, busy], [], [], [], NOW, LIM)
    assert _kinds(found) == [(H.STALLED, "T1")]
    assert "no progress for 11m" in found[0].detail and found[0].evidence["waiters"] == []


def test_a_stalled_lane_someone_waits_for_names_the_break():
    idle = H.Lane("T1", A, True, NOW - 700)
    waits = [H.Wait("T2", B, (("T1", A),))]
    (locked, stalled) = H.inspect([idle], [], waits, [], NOW, LIM)
    assert stalled.kind == H.STALLED and "ddflow claim T2 --break" in stalled.fix
    assert locked.kind == H.LOCKED and locked.item == "T2" and "T1" in locked.detail


def test_stall_limit_zero_turns_the_check_off():
    idle = H.Lane("T1", A, True, 0.0)
    assert H.inspect([idle], [], [], [], NOW, H.Limits(0, 0)) == []


def test_an_expired_lease_over_work_is_crashed_and_a_clean_one_is_not():
    lanes = [
        H.Lane("T1", A, False, NOW - 5000, work=True, worktree="/w/T1"),
        H.Lane("T2", A, False, NOW - 5000, work=None),  # could not measure: at risk
        H.Lane("T3", A, False, NOW - 5000, work=False),  # nothing to lose
    ]
    found = H.inspect(lanes, [], [], [], NOW, LIM)
    assert _kinds(found) == [(H.CRASHED, "T1"), (H.CRASHED, "T2")]
    assert (
        "ddflow recover`" in found[0].fix
        and "claim T1" in found[0].fix
        and "/w/T1" in found[0].detail
    )


def test_lanes_in_a_cycle_are_locked():
    cycle = {
        "agents": [A, B],
        "files": ["src/a.py"],
        "remedy": "agent-b yields src/a.py",
        "links": [{"item": "T1", "agent": A}, {"item": "T2", "agent": B}],
    }
    found = H.inspect([], [], [], [cycle], NOW, LIM)
    assert _kinds(found) == [(H.LOCKED, "T1"), (H.LOCKED, "T2")]
    assert found[0].evidence["via"] == "cycle" and "agent-b yields" in found[0].fix


def test_a_job_past_its_budget_is_a_runaway():
    procs = [H.Proc("J1", "T1", NOW - 4000, True), H.Proc("J2", "T1", NOW - 4000, False)]
    procs.append(H.Proc("J3", "T1", NOW - 60, True))
    found = H.inspect([], procs, [], [], NOW, LIM)
    assert _kinds(found) == [(H.RUNAWAY, "T1")] and found[0].holder == "J1"
    assert "never signals a process" in found[0].fix


# -- a real repository -------------------------------------------------------------------

IDLE, AGO = 300, 1000


def _backdate(repo, item: str, seconds: float) -> None:
    for f in (repo / ".ddflow" / "events").glob("*.jsonl"):
        out, hit = [], False
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if (
                e.get("subject") == item
            ):  # renewals too: the lease must look old to the clock as well
                if "at" in e["data"]:
                    e["data"]["at"] -= seconds
                e["ts"] = clock.iso_at(clock.epoch(e["ts"]) - seconds)
                hit = True
                out.append(json.dumps(e, separators=(",", ":")))
            else:
                out.append(line)
        if hit:
            f.write_text("\n".join(out) + "\n")


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _contended(repo, worktree: bool = False, reply_s: int = 0) -> None:
    """T1 held by A (idle AGO seconds), T2 (same file) refused for B: a recorded wait."""
    assert run_cli(repo, "init", agent=A)[0] == 0
    for k, v in (
        ("lease.hold_max_s", 3 * IDLE),
        ("lease.idle_max_s", IDLE),
        ("health.stall_s", IDLE),
        ("health.reply_s", reply_s),
    ):
        run_cli(repo, "config", k, str(v), agent=A)
    for item in ("T1", "T2"):
        run_cli(repo, "task", "add", item, "--title", item, "--globs", "src/a.py", agent=A)
    assert run_cli(repo, "claim", "T1", *([] if worktree else ["--no-worktree"]), agent=A)[0] == 0
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 3
    _backdate(repo, "T1", AGO)


def _health(repo, *argv: str):
    code, out, err = run_cli(repo, "--json", "health", *argv, agent="fleet-test")
    assert code == 0, err
    return json.loads(out)


def test_health_reports_a_stalled_contended_lease_and_writes_nothing(repo):
    _contended(repo)
    n = len(EventLog(repo, "r").read_all())
    d = _health(repo)
    assert [(f["kind"], f["item"]) for f in d["findings"]] == [("locked", "T2"), ("stalled", "T1")]
    assert d["moves"] == [] and not d["ran"]
    assert len(EventLog(repo, "r").read_all()) == n  # the view is read-only


def test_mode_off_does_nothing_and_records_no_pass(repo):
    _contended(repo)
    d = _health(repo, "--run", "--mode", "off")
    assert d["moves"] == [] and not d["ran"]
    assert not _state(repo).items["T1"].lease.expired_at
    assert "health_watch" not in _state(repo).cadences


def test_mode_propose_says_what_it_would_do_and_changes_nothing(repo):
    _contended(repo)
    d = _health(repo, "--run", "--mode", "propose")
    (move,) = d["moves"]
    assert d["ran"] and move["action"] == "break" and not move["applied"]
    assert move["line"].startswith("would park agent-a and break T1")
    assert not _state(repo).items["T1"].lease.expired_at


def test_default_mode_is_propose(repo):
    assert Config.load(repo).health.mode == "propose"
    _contended(repo)
    d = _health(repo, "--run")
    assert d["mode"] == "propose" and not _state(repo).items["T1"].lease.expired_at


def test_mode_apply_parks_then_breaks_the_stalled_lease_and_keeps_its_work(repo):
    _contended(repo, worktree=True)  # reply_s 0: the park and break follow the request at once
    lease = _state(repo).items["T1"].lease
    tree = repo / lease.worktree
    (tree / "unfinished.py").write_text("# nowhere else\n")
    old = time.time() - AGO
    os.utime(tree / "unfinished.py", (old, old))  # written long ago: no recent change to count
    d = _health(repo, "--run", "--mode", "apply")
    assert [m["applied"] for m in d["moves"]] == [True]
    st = _state(repo)
    assert st.items["T1"].lease.expired_at  # kept, expired
    broke = [e for e in EventLog(repo, "r").read_all() if e.data.get("broken_by")]
    order = [e.kind + ":" + e.subject for e in EventLog(repo, "r").read_all()]
    park, broke_at = order.index("cadence.ran:health_park:T1"), order.index("lease.expired:T1")
    assert park < broke_at  # parked before it was broken
    assert len(broke) == 1 and broke[0].data["broken_by"] == "ddflow:health"
    assert broke[0].data["broken_for"] == "T2"
    assert (tree / "unfinished.py").read_text() == "# nowhere else\n"  # nothing deleted
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", lease.branch], capture_output=True, text=True
    ).stdout
    assert lease.branch in branches


def test_apply_never_acts_on_a_lane_with_progress(repo):
    _contended(repo)
    EventLog(repo, A).append("gate.passed", "T1", {"gate": "research"})  # real work, just now
    d = _health(repo, "--run", "--mode", "apply")
    assert d["moves"] == [] and not _state(repo).items["T1"].lease.expired_at


def test_an_uncontended_idle_lane_is_stalled_but_never_broken(repo):
    _contended(repo)
    assert run_cli(repo, "remove", "T2", "--reason", "test", agent=A)[0] == 0  # nobody waits now
    d = _health(repo, "--run", "--mode", "apply")
    assert [f["kind"] for f in d["findings"]] == ["stalled"]
    assert d["moves"] == [] and not _state(repo).items["T1"].lease.expired_at


def _crashed(repo) -> tuple[Path, str]:
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "config", "lease.ttl_s", "60", agent=A)
    run_cli(repo, "config", "lease.grace_s", "0", agent=A)
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "src/a.py", agent=A)
    assert run_cli(repo, "claim", "T1", agent=A)[0] == 0
    lease = _state(repo).items["T1"].lease
    tree = repo / lease.worktree
    (tree / "finished.py").write_text("# only copy\n")
    _backdate(repo, "T1", AGO)
    return tree, lease.branch


def test_a_crashed_lane_is_marked_recoverable_in_apply_and_its_work_survives(repo):
    tree, branch = _crashed(repo)
    d = _health(repo)
    assert [(f["kind"], f["item"]) for f in d["findings"]] == [("crashed", "T1")]
    assert _health(repo, "--run", "--mode", "propose")["moves"][0]["line"].startswith(
        "would record"
    )
    assert not _state(repo).items["T1"].lease.expired_at
    d = _health(repo, "--run", "--mode", "apply")
    assert [m["action"] for m in d["moves"]] == ["expire"] and d["moves"][0]["applied"]
    assert _state(repo).items["T1"].lease.expired_at
    assert (tree / "finished.py").read_text() == "# only copy\n"
    listed = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", branch], capture_output=True, text=True
    ).stdout
    assert branch in listed  # the branch is kept as well
    assert _health(repo, "--run", "--mode", "apply")["moves"] == []  # idempotent
    assert run_cli(repo, "recover", agent=A)[0] == 0 and tree.exists()


def test_a_deadlock_is_locked_and_apply_adds_the_ordering_edge(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    for i, who in ((1, A), (2, B)):
        run_cli(
            repo, "task", "add", f"T{i}", "--title", f"T{i}", "--globs", f"src/f{i}.py", agent=A
        )
        assert run_cli(repo, "claim", f"T{i}", "--no-worktree", agent=who)[0] == 0
    assert run_cli(repo, "update", "T1", "--globs", "src/f1.py,src/f2.py", agent=A)[0] == 3
    assert run_cli(repo, "update", "T2", "--globs", "src/f2.py,src/f1.py", agent=B)[0] == 3
    d = _health(repo)
    assert {(f["kind"], f["item"]) for f in d["findings"]} == {("locked", "T1"), ("locked", "T2")}
    run_cli(repo, "config", "schedule.auto_order", "off", agent=A)  # the health mode decides
    assert _health(repo, "--run", "--mode", "propose")["moves"][0]["line"].startswith(
        "auto-order would"
    )
    assert not any(it.needs for it in _state(repo).items.values())
    d = _health(repo, "--run", "--mode", "apply")
    assert any(m["applied"] for m in d["moves"])
    assert any(it.needs for it in _state(repo).items.values())


def test_a_runaway_job_is_reported_and_its_process_is_never_signalled(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "src/a.py", agent=A)
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0
    sleeper = subprocess.Popen(["sleep", "120"])
    try:
        assert run_cli(repo, "job", "add", "T1", "--pid", str(sleeper.pid), agent=A)[0] == 0
        later = time.time() + 5 * 3600
        out = health(repo, now=later, run=True, mode="apply")
        assert [(f["kind"], f["item"]) for f in out.data["findings"]] == [("runaway", "T1")]
        assert out.data["moves"] == []
        assert sleeper.poll() is None  # still running: the watchdog never kills
    finally:
        sleeper.kill()
        sleeper.wait()


def _records(repo, prefix: str) -> list[str]:
    return [e.subject for e in EventLog(repo, "r").read_all() if e.subject.startswith(prefix)]


def test_a_finding_is_reported_once_per_key_and_acted_on_once(repo):
    _contended(repo, reply_s=300)
    first = _health(repo, "--run", "--mode", "propose")
    again = _health(repo, "--run", "--mode", "propose")
    ident = lambda d: [(f["kind"], f["item"], f["holder"], f["detail"]) for f in d["findings"]]  # noqa: E731
    assert ident(again) == ident(first)  # a read-only pass reports the same, not more
    keys = [(f["kind"], f["item"], f["holder"]) for f in first["findings"]]
    assert keys and len(keys) == len(set(keys))  # one finding per (kind, item, holder)
    assert _records(repo, "health_stop:") == []  # propose writes no request
    # apply asks once; the passes after it find the request pending and write nothing more
    for _ in range(3):
        _health(repo, "--run", "--mode", "apply")
    assert _records(repo, "health_stop:") == ["health_stop:T1"]
    assert _records(repo, "health_park:") == []  # not before reply_s
    again = _health(repo, "--run", "--mode", "apply")
    assert [m["applied"] for m in again["moves"]] == [False]
    assert "waiting up to 300s" in again["moves"][0]["line"]


def test_the_periodic_pass_falls_due_on_the_cadence_and_is_recorded(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "src/a.py", agent=A)
    cfg = Config.load(repo)

    def due(now=None):
        ctx = CAD.DueContext(repo, cfg, _state(repo), {}, now)
        return [d["cadence"] for d in HW.due(ctx)]

    assert due() == []  # no lane, nothing to watch
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0
    assert due() == ["health_watch"]  # never run
    assert _health(repo, "--run")["ran"]
    assert due() == []
    assert due(time.time() + cfg.health.interval_s + 5) == ["health_watch"]
    cfg.health.mode = "off"
    assert due(time.time() + 10**6) == []
    cfg.health.mode, cfg.health.interval_s = "propose", 0
    assert due(time.time() + 10**6) == []


def test_doctor_and_status_show_a_stalled_lane(repo):
    _contended(repo)
    _code, out, _err = run_cli(repo, "doctor", agent=A)
    assert "health: STALLED T1" in out
    _code, out, _err = run_cli(repo, "status", agent=A)
    assert "Health:" in out and "STALLED T1" in out


# -- idle, unresponsive, process-gone, per-kind modes ------------------------------------------

LIM2 = H.Limits(stall_s=3600, runaway_s=3600, idle_s=300, reply_s=120, crash_s=60)


def test_a_renewing_lane_is_idle_after_idle_s_and_stalled_only_after_stall_s():
    lane = H.Lane("T1", A, True, NOW - 400)
    assert _kinds(H.inspect([lane], [], [], [], NOW, LIM2)) == [(H.IDLE, "T1")]
    assert _kinds(H.inspect([H.Lane("T1", A, True, NOW - 100)], [], [], [], NOW, LIM2)) == []
    found = H.inspect([H.Lane("T1", A, True, NOW - 4000)], [], [], [], NOW, LIM2)
    assert _kinds(found) == [(H.STALLED, "T1")]  # STALLED supersedes IDLE
    assert H.inspect([lane], [], [], [], NOW, H.Limits(stall_s=3600, idle_s=0)) == []


def test_an_unanswered_request_makes_the_lane_unresponsive_after_reply_s():
    waits = [H.Wait("T2", B, (("T1", A),))]

    def lane(asked_ago, progress_ago=400):
        return H.Lane("T1", A, True, NOW - progress_ago, asked_at=NOW - asked_ago)

    assert not [
        f for f in H.inspect([lane(60)], [], waits, [], NOW, LIM2) if f.kind == H.UNRESPONSIVE
    ]
    (f,) = [f for f in H.inspect([lane(200)], [], waits, [], NOW, LIM2) if f.kind == H.UNRESPONSIVE]
    assert f.evidence["waiters"] == ["T2"] and "park" in f.fix
    answered = lane(200, progress_ago=100)  # progress since the request: answered
    assert not [
        f for f in H.inspect([answered], [], waits, [], NOW, LIM2) if f.kind == H.UNRESPONSIVE
    ]
    off = H.Limits(stall_s=3600, idle_s=300, reply_s=0)
    assert not [
        f for f in H.inspect([lane(9000)], [], waits, [], NOW, off) if f.kind == H.UNRESPONSIVE
    ]


def test_a_live_lease_whose_process_is_gone_is_crashed_within_crash_s():
    def gone(renewed_ago):
        return H.Lane(
            "T1", A, True, NOW - 30, work=True, renewed_at=NOW - renewed_ago, gone=True, pids=(42,)
        )

    assert H.inspect([gone(30)], [], [], [], NOW, LIM2) == []  # inside crash_s: not yet
    (f,) = H.inspect([gone(90)], [], [], [], NOW, LIM2)  # crash_s 60, TTL 1800 still running
    assert f.kind == H.CRASHED and "pid 42" in f.detail and f.evidence["pids"] == [42]
    assert H.inspect([gone(90)], [], [], [], NOW, H.Limits(crash_s=0)) == []
    clean = H.Lane("T1", A, True, NOW - 30, work=False, renewed_at=NOW - 90, gone=True)
    (f,) = H.inspect([clean], [], [], [], NOW, LIM2)  # reported; nothing to lose, so no expiry
    assert f.kind == H.CRASHED and "no work to lose" in f.detail


def test_a_dead_harness_is_crashed_in_a_real_repo_and_apply_records_the_expiry(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "config", "health.crash_s", "300", agent=A)
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "src/a.py", agent=A)
    assert run_cli(repo, "claim", "T1", agent=A)[0] == 0
    tree = repo / _state(repo).items["T1"].lease.worktree
    (tree / "finished.py").write_text("# only copy\n")
    later = time.time() + 600  # renewed ten minutes ago: the 30-minute TTL still holds it
    dead = lambda who: (False, [4242]) if who == A else (None, [])  # noqa: E731
    living = lambda who: (True, [1])  # noqa: E731
    assert [f["kind"] for f in health(repo, now=later, alive=living).data["findings"]] == []
    unknown = lambda who: (None, [])  # noqa: E731
    assert health(repo, now=later, alive=unknown).data["findings"] == []
    out = health(repo, now=later, run=True, mode="apply", alive=dead)
    assert [(f["kind"], f["item"]) for f in out.data["findings"]] == [("crashed", "T1")]
    assert [m["action"] for m in out.data["moves"]] == ["expire"]
    assert _state(repo).items["T1"].lease.expired_at
    assert (tree / "finished.py").read_text() == "# only copy\n"  # nothing deleted


def test_a_job_in_flight_is_progress_not_a_stall(repo):
    _contended(repo)
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        assert run_cli(repo, "job", "add", "T1", "--pid", str(sleeper.pid), agent=A)[0] == 0
        d = _health(repo)
        assert "stalled" not in [f["kind"] for f in d["findings"]]
    finally:
        sleeper.kill()
        sleeper.wait()


def test_each_finding_kind_has_its_own_mode(repo):
    _contended(repo, reply_s=300)
    run_cli(repo, "config", "health.mode", "propose", agent=A)
    run_cli(repo, "config", "health.mode_stalled", "apply", agent=A)
    d = _health(repo, "--run")
    assert d["modes"]["stalled"] == "apply" and d["modes"]["locked"] == "propose"
    assert d["modes"]["crashed"] == "propose"
    assert _records(repo, "health_stop:") == [
        "health_stop:T1"
    ]  # stalled acted, the rest only proposed
    run_cli(repo, "config", "health.mode_stalled", "off", agent=A)
    assert _health(repo, "--run")["modes"]["stalled"] == "off"
    assert Config.load(repo).health.mode_idle == "inherit"


def test_a_deadlock_is_ordered_when_only_the_locked_mode_applies(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    for i, who in ((1, A), (2, B)):
        run_cli(
            repo, "task", "add", f"T{i}", "--title", f"T{i}", "--globs", f"src/f{i}.py", agent=A
        )
        assert run_cli(repo, "claim", f"T{i}", "--no-worktree", agent=who)[0] == 0
    assert run_cli(repo, "update", "T1", "--globs", "src/f1.py,src/f2.py", agent=A)[0] == 3
    assert run_cli(repo, "update", "T2", "--globs", "src/f2.py,src/f1.py", agent=B)[0] == 3
    run_cli(repo, "config", "schedule.auto_order", "off", agent=A)
    run_cli(repo, "config", "health.mode_locked", "apply", agent=A)
    run_cli(repo, "config", "health.mode", "off", agent=A)
    d = _health(repo, "--run")
    assert d["ran"] and any(m["applied"] for m in d["moves"])
    assert any(it.needs for it in _state(repo).items.values())


def test_the_stop_request_reaches_the_holder_at_its_next_heartbeat(repo):
    _contended(repo, reply_s=300)
    d = _health(repo, "--run", "--mode", "apply")
    assert HW.stop_notice(_state(repo), "T1"), (d["moves"], _state(repo).cadences)
    code, out, err = run_cli(repo, "heartbeat", "T1", agent=A)
    assert code == 0, err
    assert "asked agent-a to stop" in out + err


def test_unresponsive_after_reply_s_parks_then_breaks_and_keeps_the_work(repo):
    _contended(repo, worktree=True, reply_s=300)
    tree = repo / _state(repo).items["T1"].lease.worktree
    (tree / "unfinished.py").write_text("# only copy\n")
    old = time.time() - AGO
    os.utime(tree / "unfinished.py", (old, old))
    assert [m["action"] for m in _health(repo, "--run", "--mode", "apply")["moves"]] == ["stop"]
    assert not _state(repo).items["T1"].lease.expired_at  # asked, not yet taken
    later = time.time() + 400  # the request went unanswered for reply_s
    out = health(repo, now=later, run=True, mode="apply")
    assert "unresponsive" in [f["kind"] for f in out.data["findings"]]
    assert [m["applied"] for m in out.data["moves"]] == [True]
    assert _state(repo).items["T1"].lease.expired_at
    assert _records(repo, "health_park:") == ["health_park:T1"]
    assert (tree / "unfinished.py").read_text() == "# only copy\n"
