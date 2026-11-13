"""Wait-cycle follow-ups (DX-wait-cycle-followups): the victim is the holder with the least
progress, a wait nobody is working on raises an alarm, several dropped leases are all
described, and two waits of one agent on one holder both count."""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.reporting.waitcycles import (
    progress_of,
    wait_alarms,
    wait_cycles,
    wait_findings,
    wait_moves,
)
from ddflow.config import Config
from ddflow.core import leasewait as LW
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.lanes import Move

A, B = "agent-a", "agent-b"


def _ring(pa: float, pb: float) -> list[LW.Cycle]:
    """A and B wait on each other; B's wait is the younger, so it yields when progress ties."""
    held = [LW.Held("T1", A, ("a.py",), pa), LW.Held("T2", B, ("b.py",), pb)]
    waits = [
        LW.Wait("T1", A, 1.0, ("b.py",), (("T2", B),)),
        LW.Wait("T2", B, 2.0, ("a.py",), (("T1", A),)),
    ]
    return LW.find(waits, held)


def test_the_holder_with_the_least_progress_yields_even_with_the_older_wait():
    (c,) = _ring(pa=100.0, pb=500.0)
    assert c.victim == A and c.victim_item == "T1" and c.release == ("a.py",)


def test_the_more_active_holder_is_spared():
    (c,) = _ring(pa=500.0, pb=100.0)
    assert c.victim == B


def test_equal_or_unmeasured_progress_falls_back_to_the_youngest_wait():
    assert _ring(pa=0.0, pb=0.0)[0].victim == B
    assert _ring(pa=300.0, pb=300.0)[0].victim == B


def test_an_agents_progress_is_its_newest_lease_in_the_ring():
    held = [
        LW.Held("old", A, ("a.py",), 10.0),
        LW.Held("new", A, ("c.py",), 900.0),
        LW.Held("T2", B, ("b.py",), 500.0),
    ]
    waits = [
        LW.Wait("old", A, 1.0, ("b.py",), (("T2", B),)),
        LW.Wait("T2", B, 2.0, ("a.py", "c.py"), (("old", A), ("new", A))),
    ]
    (c,) = LW.find(waits, held)
    assert c.victim == B


def test_every_dropped_lease_is_described_not_only_the_first():
    held = [
        LW.Held("alpha", "V", ("src/**", "keep.py")),
        LW.Held("beta", "V", ("conf/*.yml",)),
        LW.Held("docs", "P", ("docs/**",)),
    ]
    waits = [
        LW.Wait("check", "P", 10.0, ("src/h.py", "conf/a.yml"), (("alpha", "V"), ("beta", "V"))),
        LW.Wait("docs", "V", 20.0, ("docs/**",), (("docs", "P"),)),
    ]
    (c,) = LW.find(waits, held)
    assert [(d.item, d.release, d.keep) for d in c.drops] == [
        ("alpha", ("src/**",), ("keep.py",)),
        ("beta", ("conf/*.yml",), ()),
    ]
    rows = c.as_dict()["drops"]
    assert rows[1] == {"item": "beta", "release": ["conf/*.yml"], "keep": []}
    assert c.victim_item == "alpha" and c.keep == ("keep.py",)  # the older fields still read


def test_two_waits_of_one_agent_on_two_leases_of_one_holder_both_count():
    # P waits on two items, each behind a different lease of V: dropping only the older
    # wait's lease leaves the younger one blocked, so both leases must be in the remedy.
    held = [
        LW.Held("alpha", "V", ("a.py",)),
        LW.Held("beta", "V", ("b.py",)),
        LW.Held("p1", "P", ("p1.py",)),
        LW.Held("p2", "P", ("p2.py",)),
    ]
    waits = [
        LW.Wait("p1", "P", 10.0, ("a.py",), (("alpha", "V"),)),
        LW.Wait("p2", "P", 15.0, ("b.py",), (("beta", "V"),)),
        LW.Wait("alpha", "V", 20.0, ("p1.py",), (("p1", "P"),)),
    ]
    (c,) = LW.find(waits, held)
    assert c.victim == "V"
    assert {d.item for d in c.drops} == {"alpha", "beta"}
    assert set(c.release) == {"a.py", "b.py"} and {"p1", "p2"} <= set(c.items)
    assert "retries on p1, p2" in c.remedy
    assert c.links[0].items == ("p1", "p2") or c.links[1].items == ("p1", "p2")


def _alarm_case(since: float = 0.0, progress: float = 0.0):
    held = [LW.Held("T2", B, ("b.py", "x.py"), progress)]
    return [LW.Wait("T1", A, since, ("b.py",), (("T2", B),))], held


def test_a_wait_past_the_limit_with_nothing_running_raises_an_alarm():
    waits, held = _alarm_case()
    (a,) = LW.alarms(waits, held, [], now=2000.0, limit=1200.0)
    assert (a.item, a.agent, a.age_s) == ("T1", A, 2000.0)
    assert a.key == "wait_alarm:T1" and a.drops[0].item == "T2"
    row = a.as_dict()
    assert row["key"] == "wait_alarm:T1" and row["owner"] == B
    assert row["on"] == [{"item": "T2", "holder": B, "idle_s": 2000.0}]
    assert row["commands"] == ["ddflow update T2 --globs x.py"]
    assert row["mcp"] == ["ddflow_task action=update id=T2 globs=x.py"]
    line = a.render()
    assert line.startswith("lease wait alarm: agent-a on T1") and "T2 (agent-b, idle" in line
    assert "`ddflow update T2 --globs x.py`" in line


def test_a_young_wait_a_working_holder_and_the_off_knob_raise_nothing():
    waits, held = _alarm_case(since=1500.0)
    assert LW.alarms(waits, held, [], now=2000.0, limit=1200.0) == []  # young
    waits, held = _alarm_case(progress=1900.0)
    assert LW.alarms(waits, held, [], now=2000.0, limit=1200.0) == []  # holder works
    waits, held = _alarm_case()
    assert LW.alarms(waits, held, [], now=2000.0, limit=0) == []  # off


def test_a_wait_in_a_cycle_is_the_cycles_report_not_a_second_alarm():
    held = [LW.Held("T1", A, ("a.py",)), LW.Held("T2", B, ("b.py",))]
    waits = [
        LW.Wait("T1", A, 1.0, ("b.py",), (("T2", B),)),
        LW.Wait("T2", B, 2.0, ("a.py",), (("T1", A),)),
    ]
    assert LW.alarms(waits, held, LW.find(waits, held), now=9000.0, limit=1200.0) == []


def test_a_wait_behind_only_a_queue_place_is_no_alarm():
    waits = [LW.Wait("T1", A, 0.0, ("b.py",), (("T9", B),))]  # T9 holds no lease
    assert LW.alarms(waits, [], [], now=9000.0, limit=1200.0) == []


def test_one_lease_with_work_in_flight_silences_the_alarm():
    held = [LW.Held("T2", B, ("b.py",), 0.0), LW.Held("T3", "C", ("b.py",), 1990.0)]
    waits = [LW.Wait("T1", A, 0.0, ("b.py",), (("T2", B), ("T3", "C")))]
    assert LW.alarms(waits, held, [], now=2000.0, limit=1200.0) == []


def _setup(repo) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    for i, who in ((1, A), (2, B)):
        run_cli(repo, "task", "add", f"T{i}", "--title", f"T{i}", "--globs", f"f{i}.py", agent=A)
        assert run_cli(repo, "claim", f"T{i}", "--no-worktree", agent=who)[0] == 0


def _widen(repo, i: int, j: int, who: str) -> None:
    assert run_cli(repo, "update", f"T{i}", "--globs", f"f{i}.py,f{j}.py", agent=who)[0] == 3


def _read(repo):
    log = EventLog(repo, "reader")
    return log, fold(log.read_all(), strict=False), Config.load(repo)


def test_wait_cycles_measures_the_watermark_so_the_quiet_holder_yields(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    _widen(repo, 2, 1, B)
    log, st, cfg = _read(repo)
    assert wait_cycles(st, cfg)[0].victim == B  # no measure: the youngest wait
    now = time.time()
    work = {A: 0.0, B: now + 60}  # only B's tree shows work

    def tree(lease) -> float:
        return work[lease.holder]

    (c,) = wait_cycles(st, cfg, now, progress_of(log, st, cfg, now, activity=tree))
    assert c.victim == A


def test_a_running_job_counts_as_progress(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    log, st, cfg = _read(repo)
    now = time.time() + 5000
    lease = st.items["T2"].lease
    quiet = progress_of(log, st, cfg, now, activity=lambda _lz: 0.0)
    assert quiet("T2", lease) < now - 4000

    class Job:
        id, item, started_at, ended_at = "j1", "T2", "2026-11-05T00:00:00Z", ""

    st.jobs["j1"] = Job()
    cfg.health.runaway_s = 0
    import ddflow.api.reporting.waitcycles as WC

    real = WC.JOBS.status
    WC.JOBS.status = lambda _j: type("S", (), {"state": "running"})()
    try:
        busy = progress_of(log, st, cfg, now, activity=lambda _lz: 0.0)
        assert busy("T2", lease) == now
    finally:
        WC.JOBS.status = real


def test_progress_reads_nothing_until_asked(repo):
    _setup(repo)

    class Silent:
        root = repo

        def read_all(self):
            raise AssertionError("no wait, no read")

    st = _read(repo)[1]
    cfg = Config.load(repo)
    assert wait_cycles(st, cfg, None, progress_of(Silent(), st, cfg)) == []


def test_findings_cap_the_alarms_like_the_cycles():
    cfg = Config()
    cfg.loops.wait_cycle_shown = 1
    waits, held = _alarm_case()
    two = LW.alarms(
        [*waits, LW.Wait("T4", A, 0.0, ("b.py",), (("T2", B),))],
        held,
        [],
        now=2000.0,
        limit=1200.0,
    )
    lines = wait_findings([], cfg, two)
    assert len(lines) == 2 and lines[1] == "1 more lease wait alarm(s)"


def test_status_and_doctor_report_a_cycle(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    _widen(repo, 2, 1, B)
    _, out, _ = run_cli(repo, "status", agent=A)
    assert "lease deadlock: agent-a -> agent-b -> agent-a" in out
    data = json.loads(run_cli(repo, "--json", "status", agent=A)[1])
    assert data["wait_cycles"][0]["agents"] == [A, B] and data["wait_cycles"][0]["drops"]


def test_status_is_silent_with_no_cycle_or_alarm(repo):
    _setup(repo)
    data = json.loads(run_cli(repo, "--json", "status", agent=A)[1])
    assert "wait_cycles" not in data and "wait_alarms" not in data


def test_status_and_doctor_raise_the_alarm(repo):
    _setup(repo)
    assert run_cli(repo, "config", "loops.wait_alarm_s", "1", agent=A)[0] == 0
    _widen(repo, 1, 2, A)
    time.sleep(1.2)
    _, out, _ = run_cli(repo, "status", agent=A)
    assert "lease wait alarm: agent-a on T1" in out
    data = json.loads(run_cli(repo, "--json", "status", agent=A)[1])
    assert data["wait_alarms"][0]["key"] == "wait_alarm:T1"
    code, out, _ = run_cli(repo, "doctor", agent=A)
    assert code == 1 and "lease wait alarm: agent-a on T1" in out


def test_the_alarm_needs_no_cycle_report_turned_on(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    log, st, cfg = _read(repo)
    cfg.loops.wait_cycle = False
    cfg.loops.wait_alarm_s = 1
    now = time.time() + 5
    progress = progress_of(log, st, cfg, now, activity=lambda _lz: 0.0)
    assert [a.item for a in wait_alarms(st, cfg, [], progress, now)] == ["T1"]


def test_the_brief_names_a_deadlock(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    _widen(repo, 2, 1, B)
    _, out, _ = run_cli(repo, "brief", agent=A)
    assert "- lease deadlock: agent-a -> agent-b -> agent-a" in out


def test_the_brief_raises_the_alarm(repo):
    _setup(repo)
    assert run_cli(repo, "config", "loops.wait_alarm_s", "1", agent=A)[0] == 0
    _widen(repo, 1, 2, A)
    time.sleep(1.2)
    _, out, _ = run_cli(repo, "brief", agent=A)
    assert "- lease wait alarm: agent-a on T1" in out


def test_stuck_waits_rank_behind_lost_work_and_ahead_of_the_rest():
    (c,) = _ring(pa=100.0, pb=500.0)
    (a,) = LW.alarms(*_alarm_case(), [], now=2000.0, limit=1200.0)
    resume, idle = Move("resume", "r"), Move("idle", "i")
    moves = wait_moves([resume, idle], [c], [a], 9)
    assert [m.kind for m in moves] == ["resume", "deadlock", "wait-alarm", "idle"]
    assert moves[1].command == "ddflow release T1" and moves[1].lanes == (A, B)
    assert moves[2].command == "ddflow update T2 --globs x.py"
    assert [m.kind for m in wait_moves([resume, idle], [c], [a], 2)] == ["resume", "deadlock"]


def test_a_move_cannot_carry_structure_out_of_a_hostile_name():
    held = [LW.Held("T1", "a`b", ("a.py",)), LW.Held("T2", B, ("b.py",))]
    waits = [
        LW.Wait("T1", "a`b", 1.0, ("b.py",), (("T2", B),)),
        LW.Wait("T2", B, 2.0, ("a.py",), (("T1", "a`b"),)),
    ]
    (m,) = wait_moves([], LW.find(waits, held), [], 9)
    assert "`" not in m.text and all("`" not in n for n in m.lanes)


def test_a_lease_in_a_ring_and_an_alarm_is_measured_once(repo):
    _setup(repo)
    log, st, cfg = _read(repo)
    calls: list[str] = []

    def tree(lease) -> float:
        calls.append(lease.holder)
        return 0.0

    progress = progress_of(log, st, cfg, activity=tree)
    lease = st.items["T2"].lease
    assert progress("T2", lease) == progress("T2", lease)
    assert calls == [B]


def test_the_bounded_status_cuts_the_wait_rows_and_says_so():
    from ddflow.api.reporting import overview as OV

    many = [{"key": str(i)} for i in range(OV.STATUS_LIST_LIMIT + 3)]
    lists = ("completed_tasks", "in_flight", "ready_now", "held_by_cap", "interrupted")
    data = {k: [] for k in (*lists, "loops", "recoverable")}
    data |= {"wait_cycles": list(many), "wait_alarms": list(many)}
    OV._bound(data)
    assert len(data["wait_alarms"]) == len(data["wait_cycles"]) == OV.STATUS_LIST_LIMIT
    assert data["truncated"]["lists"]["wait_alarms"] == len(many)


def test_an_alarm_on_two_leases_gives_every_command_on_the_wire():
    held = [LW.Held("L1", B, ("a.py",)), LW.Held("L2", B, ("b.py", "c.py"))]
    waits = [LW.Wait("T1", A, 0.0, ("a.py", "b.py"), (("L1", B), ("L2", B)))]
    (a,) = LW.alarms(waits, held, [], now=2000.0, limit=1200.0)
    row = a.as_dict()
    assert row["commands"] == ["ddflow release L1", "ddflow update L2 --globs c.py"]
    assert row["mcp"] == ["ddflow_release id=L1", "ddflow_task action=update id=L2 globs=c.py"]


def test_a_kept_glob_survives_the_shell():
    cmd = LW.Drop("T1", ("a.py",), ("src/**", "my file.py")).command()
    assert shlex.split(cmd) == ["ddflow", "update", "T1", "--globs", "src/**,my file.py"]


def test_a_command_safe_would_change_is_not_offered_to_run():
    held = [LW.Held("T1", A, ("a.py", "src/**")), LW.Held("T2", B, ("b.py",), 500.0)]
    waits = [
        LW.Wait("T1", A, 1.0, ("b.py",), (("T2", B),)),
        LW.Wait("T2", B, 2.0, ("a.py",), (("T1", A),)),
    ]
    (m,) = wait_moves([], LW.find(waits, held), [], 9)
    assert m.command == "ddflow doctor"  # the quotes `safe` strips are what stop the shell


def test_the_time_comes_from_the_clock_port(repo, monkeypatch):
    _setup(repo)
    _widen(repo, 1, 2, A)
    log, st, cfg = _read(repo)
    cfg.loops.wait_alarm_s = 1
    from ddflow.api.reporting import waitcycles as WC

    later = time.time() + 5

    class Later:
        def wall(self) -> float:
            return later

    monkeypatch.setattr(WC, "CLOCK", Later())
    _cycles, alarms = WC.wait_report(log, st, cfg, activity=lambda _lz: 0.0)
    assert [a.item for a in alarms] == ["T1"]


def test_lanes_advise_names_a_deadlock_and_its_commands(repo):
    _setup(repo)
    _widen(repo, 1, 2, A)
    _widen(repo, 2, 1, B)
    data = json.loads(run_cli(repo, "--json", "lanes", "advise", agent=A)[1])
    (move,) = [m for m in data["moves"] if m["kind"] == "deadlock"]
    assert "agent-a -> agent-b -> agent-a" in move["text"]
    assert move["command"].startswith("ddflow ") and "[deadlock]" in data["text"]


def test_lanes_advise_raises_the_alarm(repo):
    _setup(repo)
    assert run_cli(repo, "config", "loops.wait_alarm_s", "1", agent=A)[0] == 0
    _widen(repo, 1, 2, A)
    time.sleep(1.2)
    data = json.loads(run_cli(repo, "--json", "lanes", "advise", agent=A)[1])
    assert [m["kind"] for m in data["moves"]].count("wait-alarm") == 1


def test_doctor_json_carries_the_alarm_and_says_none_when_there_is_none(repo):
    _setup(repo)
    assert json.loads(run_cli(repo, "--json", "doctor", agent=A)[1])["wait_alarms"] == []
    assert run_cli(repo, "config", "loops.wait_alarm_s", "1", agent=A)[0] == 0
    _widen(repo, 1, 2, A)
    time.sleep(1.2)
    rows = json.loads(run_cli(repo, "--json", "doctor", agent=A)[1])["wait_alarms"]
    assert rows[0]["key"] == "wait_alarm:T1" and rows[0]["commands"]


def test_an_item_id_survives_the_shell_too():
    hostile = "x; touch pwn"
    kept = LW.Drop(hostile, ("a.py",), ("b.py",)).command()
    assert shlex.split(kept) == ["ddflow", "update", hostile, "--globs", "b.py"]
    gone = LW.Drop(hostile, ("a.py",), ()).command()
    assert shlex.split(gone) == ["ddflow", "release", hostile]


def test_a_victim_blocked_on_two_leases_is_told_to_wait_for_both():
    held = [
        LW.Held("b1", B, ("b1.py",), 900.0),
        LW.Held("b2", B, ("b2.py",), 900.0),
        LW.Held("a3", A, ("a3.py",), 100.0),
    ]
    waits = [
        LW.Wait("a3", A, 1.0, ("b1.py", "b2.py"), (("b1", B), ("b2", B))),
        LW.Wait("b1", B, 2.0, ("a3.py",), (("a3", A),)),
    ]
    (c,) = LW.find(waits, held)
    assert c.victim == A
    assert "`ddflow wait --item b1`" in c.remedy and "`ddflow wait --item b2`" in c.remedy


def test_creating_a_hyphen_id_is_refused_and_a_valid_id_drives_end_to_end(repo):
    assert run_cli(repo, "init", agent=A)[0] == 0
    # A leading hyphen is refused at creation (B-item-id-leading-hyphen): the id would
    # then be parsed as an option by every command that names it. A remedy line still
    # protects a hyphen id that is already in the queue (recovered/legacy): Drop routes
    # it after `--` and wait uses `--item=` (pinned in the tests that follow). Drive an
    # id the adders accept end to end through the real parser here.
    add = ("task", "add", "--title", "t", "--globs", "a.py,b.py", "--", "-x")
    _, _, err = run_cli(repo, *add, agent=A)
    assert "may not start with a hyphen" in err
    good = ("task", "add", "--title", "t", "--globs", "a.py,b.py", "--", "x")
    assert run_cli(repo, *good, agent=A)[0] == 0
    assert run_cli(repo, "claim", "--no-worktree", "--", "x", agent=A)[0] == 0
    update = LW.Drop("x", ("a.py",), ("b.py",)).command()
    assert run_cli(repo, *shlex.split(update)[1:], agent=A)[0] == 0
    show = json.loads(run_cli(repo, "--json", "show", "--", "x", agent=A)[1])
    assert show["globs"] == ["b.py"]
    release = LW.Drop("x", ("b.py",), ()).command()
    assert run_cli(repo, *shlex.split(release)[1:], agent=A)[0] == 0
    show = json.loads(run_cli(repo, "--json", "show", "--", "x", agent=A)[1])
    assert show["state"] == "open"


def test_a_drop_for_a_hyphen_id_routes_it_past_the_option_parser():
    assert shlex.split(LW.Drop("-x", ("a.py",), ("b.py",)).command()) == [
        "ddflow",
        "update",
        "--globs",
        "b.py",
        "--",
        "-x",
    ]
    assert shlex.split(LW.Drop("-x", ("b.py",), ()).command()) == [
        "ddflow",
        "release",
        "--",
        "-x",
    ]


def test_the_wait_in_a_remedy_keeps_a_hyphen_id_a_value():
    held = [LW.Held("-b", B, ("b.py",)), LW.Held("a3", A, ("a3.py",))]
    waits = [
        LW.Wait("a3", A, 2.0, ("b.py",), (("-b", B),)),
        LW.Wait("-b", B, 1.0, ("a3.py",), (("a3", A),)),
    ]
    (c,) = LW.find(waits, held)
    assert c.victim == A and "`ddflow wait --item=-b`" in c.remedy
