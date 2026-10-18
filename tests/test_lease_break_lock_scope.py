"""A claim judges a lease's progress BEFORE the append lock, not inside it
(DX-lease-break-lock-scope): the worktree activity behind the verdict is a `git` call, and
made under the lock it stalls every other writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import clock
from ddflow.infra import log as LG
from ddflow.infra.log import EventLog
from ddflow.services import leases

A, B = "agent-a", "agent-b"
IDLE, AGO = 300, 1000


def _backdate(repo: Path, item: str, seconds: float) -> None:
    for f in (repo / ".ddflow" / "events").glob("*.jsonl"):
        out = []
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if e.get("subject") == item and e.get("kind") != "lease.renewed":
                if "at" in e["data"]:
                    e["data"]["at"] -= seconds
                e["ts"] = clock.iso_at(clock.epoch(e["ts"]) - seconds)
            out.append(json.dumps(e, separators=(",", ":")))
        f.write_text("\n".join(out) + "\n")


def _setup(repo: Path) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    run_cli(repo, "config", "lease.hold_max_s", str(3 * IDLE), agent=A)
    run_cli(repo, "config", "lease.idle_max_s", str(IDLE), agent=A)
    for item in ("T1", "T2"):
        run_cli(repo, "task", "add", item, "--title", item, "--globs", "src/a.py", agent=A)
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0
    _backdate(repo, "T1", AGO)


def test_claim_break_runs_no_activity_probe_under_the_append_lock(repo):
    _setup(repo)
    log = EventLog(repo, B)
    cfg = Config.load(repo)
    held_during: list[bool] = []

    def probe(_lease) -> float:
        # the stand-in for `git log` / `git status` of the holder's worktree
        held_during.append(bool(LG._HELD.get(LG._held_key(log.lock_path), 0)))
        return 0.0

    broke: list[dict] = []
    got = leases.acquire(log, cfg, "T2", holder=B, break_lease=True, activity=probe, broke=broke)
    assert got.holder == B and [b["item"] for b in broke] == ["T1"]
    assert held_during and not any(held_during), "activity was read while the lock was held"


def test_a_log_that_moved_before_the_lock_is_rejudged_from_a_fresh_read(repo):
    """The pre-read is only used while the log is unchanged: a progress event that landed in
    between must still save the holder."""
    _setup(repo)
    log = EventLog(repo, B)
    cfg = Config.load(repo)
    real = leases._prejudge

    def moving(*args, **kw):
        pre = real(*args, **kw)
        EventLog(repo, A).append("gate.passed", "T1", {"gate": "research"})  # after the pre-read
        return pre

    leases._prejudge = moving
    try:
        try:
            leases.acquire(log, cfg, "T2", holder=B, break_lease=True, activity=lambda _l: 0.0)
        except leases.LeaseError as exc:
            assert "T1" in str(exc)
        else:
            raise AssertionError("a holder with fresh progress must not be broken")
    finally:
        leases._prejudge = real


def _claim_with_a_late_event(repo: Path, monkeypatch, fresh_s: float | None = None):
    """Claim T2 --break with an unrelated event landing between the pre-lock read and the lock;
    return whether the activity probe ever ran with the lock held."""
    _setup(repo)
    log, cfg = EventLog(repo, B), Config.load(repo)
    held_during: list[bool] = []
    real = leases._prejudge

    def probe(_lease) -> float:
        held_during.append(bool(LG._HELD.get(LG._held_key(log.lock_path), 0)))
        return 0.0

    def moving(*args, **kw):
        pre = real(*args, **kw)
        EventLog(repo, A).append("session.note", "T9", {"text": "elsewhere"})
        return pre

    monkeypatch.setattr(leases, "_prejudge", moving)
    if fresh_s is not None:
        monkeypatch.setattr("ddflow.services.leasebreak.FRESH_S", fresh_s)
    leases.acquire(log, cfg, "T2", holder=B, break_lease=True, activity=probe, broke=[])
    return held_during


def test_an_unrelated_append_in_the_wait_still_keeps_git_out_of_the_lock(repo, monkeypatch):
    held = _claim_with_a_late_event(repo, monkeypatch)
    assert held and not any(held)


def test_a_stale_worktree_reading_is_probed_again_rather_than_trusted(repo, monkeypatch):
    """A holder that resumed while the claim waited for the lock must not be broken on a
    reading from before: it asks git again (under the lock, slower, never wrong)."""
    held = _claim_with_a_late_event(repo, monkeypatch, fresh_s=-1.0)
    assert any(held)
