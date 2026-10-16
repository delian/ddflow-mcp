"""A contended lease that shows no PROGRESS is breakable (D-lease-hold-limit,
DX-lease-progress-breakable).

2026-10-15: three agents sat 45+ minutes on current leases because a heartbeat loop renews
an idle lease. Liveness is not progress: a lease is judged by commits, gate outcomes,
notes and changed files, never by its heartbeats, and only while another agent wants its
files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.events import Event
from ddflow.core.model import Lease, fold
from ddflow.infra.log import EventLog
from ddflow.services import leasebreak as LB

A, B = "agent-a", "agent-b"
NOW = 1_000_000.0


def _lease(acquired: float = NOW - 5000) -> Lease:
    return Lease(holder=A, acquired_at=acquired, renewed_at=NOW, ttl_s=1800, globs=["src/a.py"])


def _cfg(idle: int = 900, hold: int = 3600):
    cfg = Config()
    cfg.lease.idle_max_s, cfg.lease.hold_max_s = idle, hold
    return cfg


def _beats(n: int) -> list[Event]:
    return [
        Event("lease.renewed", "T1", {"holder": A}, ts=clock_iso(NOW - i * 60)) for i in range(n)
    ]


def clock_iso(t: float) -> str:
    return clock.iso_at(t)


def _none(_lease: Lease) -> float:
    return 0.0


def test_a_heartbeat_only_holder_is_yield_requested_then_breakable():
    beats = _beats(50)  # fifty renewals in the last hour: alive, doing nothing
    v = LB.judge(_cfg(), beats, "T1", _lease(NOW - 1000), ["T2"], NOW, _none)
    assert v and v.yield_requested and not v.breakable  # idle 1000 s >= idle_max_s 900
    v = LB.judge(_cfg(), beats, "T1", _lease(NOW - 4000), ["T2"], NOW, _none)
    assert v and v.breakable and v.idle_s == 4000


def test_a_progressing_holder_is_never_asked_or_broken():
    gate = Event("gate.passed", "T1", {"gate": "research"}, ts=clock_iso(NOW - 60))
    assert (
        LB.judge(_cfg(), [*_beats(50), gate], "T1", _lease(NOW - 9000), ["T2"], NOW, _none) is None
    )
    # a commit or a changed file in the worktree is progress too
    assert LB.judge(_cfg(), [], "T1", _lease(NOW - 9000), ["T2"], NOW, lambda _l: NOW - 30) is None


def test_an_uncontended_lease_is_never_judged_however_old():
    assert LB.judge(_cfg(), _beats(3), "T1", _lease(NOW - 10**6), [], NOW, _none) is None


def test_a_hold_limit_below_the_idle_limit_still_asks_before_it_breaks():
    v = LB.judge(_cfg(idle=900, hold=600), [], "T1", _lease(NOW - 700), ["T2"], NOW, _none)
    assert v and v.yield_requested and v.breakable  # breakable always implies asked


def test_zero_limits_are_the_old_behaviour():
    assert LB.judge(_cfg(0, 0), [], "T1", _lease(NOW - 10**6), ["T2"], NOW, _none) is None


IDLE, AGO = 300, 1000  # wide window, elapsed time backdated (AGO < the 1800 s lease ttl)


def _backdate(repo, item: str, seconds: float) -> None:
    """Make ``item``'s lease look ``seconds`` older by rewriting the time the log records
    for it (add, claim and start), so no test depends on how long a subprocess takes to start."""
    for f in (repo / ".ddflow" / "events").glob("*.jsonl"):
        out, hit = [], False
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if e.get("subject") == item and e.get("kind") != "lease.renewed":
                if "at" in e["data"]:
                    e["data"]["at"] -= seconds
                e["ts"] = clock.iso_at(clock.epoch(e["ts"]) - seconds)
                hit = True
                out.append(json.dumps(e, separators=(",", ":")))
            else:
                out.append(line)
        if hit:
            f.write_text("\n".join(out) + "\n")


def _setup(repo, hold: int = 3 * IDLE) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "config", "lease.hold_max_s", str(hold), agent=A)
    run_cli(repo, "config", "lease.idle_max_s", str(IDLE), agent=A)
    for item in ("T1", "T2"):
        assert (
            run_cli(repo, "task", "add", item, "--title", item, "--globs", "src/a.py", agent=A)[0]
            == 0
        )
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def test_claim_breaks_a_heartbeat_only_lease_and_tells_both_agents(repo):
    _setup(repo)
    assert run_cli(repo, "claim", "T2", "--no-worktree", "--break", agent=B)[0] == 3  # fresh
    _backdate(repo, "T1", AGO)
    run_cli(repo, "heartbeat", "T1", agent=A)  # alive, no work
    code, out, _err = run_cli(repo, "claim", "T2", "--no-worktree", "--break", agent=B)
    assert code == 0 and "BROKE T1" in out  # the claimant is told
    st = _state(repo)
    assert st.items["T2"].lease.holder == B
    assert st.items["T1"].lease.expired_at  # kept, expired: its worktree/branch pointer survives
    broken = [e for e in EventLog(repo, "r").read_all() if e.data.get("broken_by")]
    assert len(broken) == 1 and broken[0].data["broken_for"] == "T2"
    assert broken[0].data["broken_by"] == B and broken[0].data["idle_s"] >= IDLE
    code, out, err = run_cli(repo, "heartbeat", "T1", agent=A)  # the holder is told
    assert code != 0 and "BROKEN" in out + err


def test_a_slow_start_does_not_make_a_fresh_lease_breakable(repo):
    _setup(repo)
    _backdate(repo, "T1", 150)  # a loaded machine: minutes pass before the claim runs
    assert run_cli(repo, "claim", "T2", "--no-worktree", "--break", agent=B)[0] == 3
    assert not _state(repo).items["T1"].lease.expired_at
    _backdate(repo, "T1", AGO)
    assert run_cli(repo, "claim", "T2", "--no-worktree", "--break", agent=B)[0] == 0


def test_a_progressing_holder_keeps_its_lease_against_a_claim(repo):
    _setup(repo)
    _backdate(repo, "T1", AGO)  # the lease is older than hold_max_s ...
    EventLog(repo, A).append(
        "gate.passed", "T1", {"gate": "research"}
    )  # ... but real work just landed
    code, _out, err = run_cli(repo, "claim", "T2", "--no-worktree", agent=B)
    assert code == 3 and "held by" in err
    assert not _state(repo).items["T1"].lease.expired_at


def test_break_flag_is_required_unless_break_on_timeout_is_on(repo):
    _setup(repo)
    _backdate(repo, "T1", AGO)
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 3  # the default: deliberate
    run_cli(repo, "config", "lease.break_on_timeout", "true", agent=A)
    assert (
        run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 0
    )  # on: a plain claim breaks


def test_an_uncontended_old_lease_survives_an_unrelated_claim(repo):
    _setup(repo)
    run_cli(repo, "task", "add", "T3", "--title", "T3", "--globs", "src/c.py", agent=A)
    _backdate(repo, "T1", AGO)
    assert run_cli(repo, "claim", "T3", "--no-worktree", "--break", agent=B)[0] == 0
    assert not _state(repo).items["T1"].lease.expired_at


def test_the_holder_is_asked_to_yield_before_anything_is_broken(repo):
    _setup(repo, hold=10 * AGO)  # asked after IDLE, broken only after 10 * AGO
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 3  # records the wait
    _backdate(repo, "T1", AGO)
    code, out, err = run_cli(repo, "heartbeat", "T1", agent=A)
    assert code == 0 and "YIELD REQUESTED" in out + err and "T2" in out + err
    code, out, _err = run_cli(repo, "brief", agent=A)
    assert "Yield requested" in out
    assert not _state(repo).items["T1"].lease.expired_at  # asked, not broken


def test_a_claim_that_cannot_break_everything_breaks_nothing(repo):
    _setup(repo)
    _backdate(repo, "T1", AGO)  # T1 (src/a.py) is now idle; T3 below is fresh
    run_cli(repo, "task", "add", "T3", "--title", "T3", "--globs", "src/b.py", agent=A)
    assert run_cli(repo, "claim", "T3", "--no-worktree", agent="agent-c")[0] == 0
    run_cli(repo, "update", "T2", "--globs", "src/a.py,src/b.py", agent=A)
    assert run_cli(repo, "claim", "T2", "--no-worktree", "--break", agent=B)[0] == 3
    assert not _state(repo).items["T1"].lease.expired_at  # no half-broken claim


def test_a_deleted_file_is_progress(tmp_path):
    import subprocess

    from ddflow.infra import treeactivity as TA

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "gone.py").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "c",
        ],
        check=True,
    )
    before = TA.last_change_at(tmp_path)
    (tmp_path / "gone.py").unlink()
    assert before == 0.0 and TA.last_change_at(tmp_path) > 0.0


def test_an_unreadable_tree_is_never_read_as_idle(tmp_path):
    from ddflow.infra import treeactivity as TA

    assert TA.last_change_at(tmp_path) == float("inf")  # not a git tree: git cannot say
