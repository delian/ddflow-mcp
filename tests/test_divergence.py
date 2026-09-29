"""Offline divergence is surfaced, never settled by tie-break (B191).

Bug Bbe8ea3c684. Two clones that each `task add T2` with different content, or each
claim the same item, merge cleanly under `merge=union` -- and the fold then kept one:
`_h_added` re-applied every field of the later-lamport add, and `_h_lease_acquired`
replaced the lease. The other definition and the other claim vanished and `doctor` said
Healthy. Since B-add-refuses-dup-id one log refuses a second add of a live id, so a rival
add can only arrive by a MERGE; that is exactly the case the fold cannot refuse and must
therefore record.

The two-clone probes are the real thing: a bare remote, two clones, `git pull` with the
union driver `ddflow adopt` installs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ddflow.api import reporting
from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.core.schedule import plan
from ddflow.infra.log import EventLog
from ddflow.services import leases as L
from tests.conftest import run_cli


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *argv], check=True, capture_output=True, text=True
    ).stdout


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _push(repo: Path, msg: str) -> None:
    _commit_log(repo, msg)
    _git(repo, "push", "-q", "origin", "main")


def _pull(repo: Path, msg: str) -> None:
    _commit_log(repo, msg)
    _git(repo, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")


@pytest.fixture
def two_clones(tmp_path: Path) -> tuple[Path, Path]:
    """Clone A files T1 and pushes; clone B pulls. Each then writes as its own agent."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert run_cli(a, "task", "add", "T1", "--title", "shared", agent="alice")[0] == 0
    _push(a, "T1")
    return a, _clone(remote, tmp_path / "b")


@pytest.fixture
def rival_adds(two_clones) -> Path:
    """Both clones file T2, differently, offline; B pulls A's. Returns B."""
    a, b = two_clones
    assert (
        run_cli(a, "task", "add", "T2", "--title", "alpha", "--body", "A's T2", agent="alice")[0]
        == 0
    )
    _push(a, "alice T2")
    assert (
        run_cli(b, "task", "add", "T2", "--title", "beta", "--body", "B's T2", agent="bob")[0] == 0
    )
    _pull(b, "bob T2")
    return b


@pytest.fixture
def double_claim(two_clones) -> Path:
    """Both clones claim T1 offline; B pulls A's. Returns B."""
    a, b = two_clones
    assert run_cli(a, "claim", "T1", "--no-worktree", agent="alice")[0] == 0
    _push(a, "alice claims")
    assert run_cli(b, "claim", "T1", "--no-worktree", agent="bob")[0] == 0
    _pull(b, "bob claims")
    return b


def _show(repo: Path, item: str) -> dict:
    code, out, err = run_cli(repo, "--json", "show", item, agent="bob")
    assert code == 0, out + err
    return json.loads(out)


def _state(repo: Path):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


# -- (a) the fold records the contest ----------------------------------------------------


def test_two_clones_adding_one_id_leave_a_contest_not_a_winner(rival_adds):
    """FAILED before the fix: T2 folded to 'beta' with no trace of 'alpha'."""
    item = _show(rival_adds, "T2")
    assert sorted(d["title"] for d in item["contested"]) == ["alpha", "beta"]
    assert sorted(d["agent"] for d in item["contested"]) == ["alice", "bob"]
    assert all(d["event"].startswith("e") for d in item["contested"])


def test_two_clones_claiming_one_item_leave_a_lease_contest(double_claim):
    """FAILED before the fix: one lease survived and the other claim vanished."""
    item = _show(double_claim, "T1")
    assert sorted(h["holder"] for h in item["lease_contest"]) == ["alice", "bob"]


def test_doctor_reports_each_contest_as_a_problem_with_the_remedy(rival_adds):
    """FAILED before the fix: doctor said Healthy over a lost definition."""
    out = reporting.doctor(rival_adds, agent="bob")
    assert out.exit == 1
    hits = [p for p in out.data["problems"] if "T2" in p and "CONTESTED" in p]
    assert hits and "ddflow resolve T2 --keep" in hits[0], out.data["problems"]


def test_doctor_reports_a_double_claim(double_claim):
    out = reporting.doctor(double_claim, agent="bob")
    hits = [p for p in out.data["problems"] if "T1" in p and "CONTESTED" in p]
    assert hits and "alice" in hits[0] and "bob" in hits[0], out.data["problems"]


def test_next_does_not_offer_a_contested_item(rival_adds):
    p = plan(_state(rival_adds), Config.load(rival_adds), agent="carol")
    assert "T2" not in [i.id for i in p.ready]
    why = next(b for b in p.blocked if b.item == "T2")
    assert why.reason == "contested" and "alice" in why.detail and "bob" in why.detail


def test_show_marks_the_item_contested(rival_adds):
    code, out, err = run_cli(rival_adds, "show", "T2", agent="bob")
    assert code == 0, err
    assert "CONTESTED" in out and "alpha" in out and "beta" in out


# -- non-contests: every legitimate path to a second add or a new holder ---------------


def _ev(kind: str, subject: str, lamport: int, agent: str = "x", **data) -> Event:
    e = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=f"t{lamport}")
    return Event(**{**e.__dict__, "id": e.compute_id()})


def _acq(lamport: int, holder: str, at: float, ttl: int = 100) -> Event:
    return _ev("lease.acquired", "T", lamport, holder, holder=holder, at=at, ttl_s=ttl)


def test_readding_a_removed_item_is_not_a_contest():
    st = fold(
        [
            _ev("task.added", "T", 1, title="one"),
            _ev("task.removed", "T", 2),
            _ev("task.added", "T", 3, title="two"),
        ]
    )
    assert st.items["T"].contested == [] and st.items["T"].title == "two"


def test_a_removed_contested_item_is_no_longer_contested_once_readded():
    st = fold(
        [
            _ev("task.added", "T", 1, "a", title="one"),
            _ev("task.added", "T", 1, "b", title="two"),
            _ev("task.removed", "T", 2),
            _ev("task.added", "T", 3, title="three"),
        ]
    )
    assert st.items["T"].contested == []


def test_the_same_definition_arriving_twice_is_not_a_contest():
    st = fold(
        [_ev("task.added", "T", 1, "a", title="one"), _ev("task.added", "T", 2, "b", title="one")]
    )
    assert st.items["T"].contested == []


def test_claim_release_claim_by_another_agent_is_not_a_contest():
    st = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            _acq(2, "a", at=1000.0),
            _ev("lease.released", "T", 3, "a", holder="a"),
            _acq(4, "b", at=1001.0),
        ]
    )
    assert st.items["T"].lease_contest == [] and st.items["T"].lease.holder == "b"


def test_taking_over_a_recorded_expiry_is_not_a_contest():
    st = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            _acq(2, "a", at=1000.0),
            _ev("lease.expired", "T", 3, holder="a"),
            _acq(4, "b", at=1010.0),
        ]
    )
    assert st.items["T"].lease_contest == [] and st.items["T"].lease.holder == "b"


def test_the_holders_own_reacquire_is_not_a_contest():
    st = fold([_ev("task.added", "T", 1, title="t"), _acq(2, "a", 1000.0), _acq(3, "a", 1010.0)])
    assert st.items["T"].lease_contest == []


def test_a_forced_claim_over_a_lapsed_lease_is_not_a_contest(repo: Path, monkeypatch):
    """`--force` may take a lease past its TTL with no `lease.expired` recorded; the fold
    must see from the times that the first lease had lapsed."""
    cfg = Config.load(repo)
    log = EventLog(repo, "a")
    log.append("task.added", "T", {"title": "t"})
    clock = [1_000_000.0]
    monkeypatch.setattr(L.time, "time", lambda: clock[0])
    L.acquire(log, cfg, "T", holder="a")
    clock[0] += cfg.lease.ttl_s + cfg.lease.grace_s + 5
    L.acquire(EventLog(repo, "b"), cfg, "T", holder="b", force=True)
    it = fold(log.read_all()).items["T"]
    assert it.lease.holder == "b" and it.lease_contest == []


def test_a_contestant_releasing_ends_the_lease_contest():
    """The loser gave up: nothing is left to resolve, and the remaining claim stands."""
    st = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            _acq(2, "a", 1000.0),
            _acq(2, "b", 1001.0),
            _ev("lease.released", "T", 3, "b", holder="b"),
        ]
    )
    it = st.items["T"]
    assert it.lease_contest == [] and it.lease is not None and it.lease.holder == "a"


# -- (c) resolve --------------------------------------------------------------------------


def test_resolve_keeps_one_definition_and_refiles_the_other(rival_adds):
    b = rival_adds
    alice = next(d for d in _show(b, "T2")["contested"] if d["agent"] == "alice")
    code, out, err = run_cli(
        b, "resolve", "T2", "--keep", alice["event"], "--refile-as", "T2b", agent="bob"
    )
    assert code == 0, out + err
    kept, refiled = _show(b, "T2"), _show(b, "T2b")
    assert kept["contested"] == [] and kept["title"] == "alpha" and kept["body"] == "A's T2"
    assert refiled["title"] == "beta" and refiled["body"] == "B's T2"
    assert reporting.doctor(b, agent="bob").data["problems"] == []
    kinds = [e.kind for e in EventLog(b, "probe").read_all()]
    assert "item.resolved" in kinds


def test_resolve_by_agent_prints_how_to_refile_the_loser(rival_adds):
    code, out, err = run_cli(rival_adds, "resolve", "T2", "--keep", "bob", agent="bob")
    assert code == 0, out + err
    assert "alpha" in out and "A's T2" in out and "ddflow task add" in out
    assert _show(rival_adds, "T2")["title"] == "beta"


def test_resolve_names_the_lease_holder_and_releases_the_other(double_claim):
    b = double_claim
    code, out, err = run_cli(b, "resolve", "T1", "--keep", "alice", agent="bob")
    assert code == 0, out + err
    item = _show(b, "T1")
    assert item["lease_contest"] == [] and item["lease"]["holder"] == "alice"
    released = [
        e.data.get("holder") for e in EventLog(b, "probe").read_all() if e.kind == "lease.released"
    ]
    assert released == ["bob"]


def test_resolve_refuses_an_item_that_is_not_contested(two_clones):
    a, _ = two_clones
    code, _out, err = run_cli(a, "resolve", "T1", "--keep", "alice", agent="alice")
    assert code == 3, err
    assert "not contested" in err


def test_resolve_fails_on_a_keep_that_names_no_contestant(rival_adds):
    code, _out, err = run_cli(rival_adds, "resolve", "T2", "--keep", "zed", agent="bob")
    assert code == 1 and "alice" in err and "bob" in err


def test_replay_carries_the_resolution(rival_adds):
    """`replay` rebuilds the project from the log; a contest settled off the record would
    come back as two definitions and no decision between them."""
    b = rival_adds
    alice = next(d for d in _show(b, "T2")["contested"] if d["agent"] == "alice")
    assert run_cli(b, "resolve", "T2", "--keep", alice["event"], agent="bob")[0] == 0
    code, out, err = run_cli(b, "replay")
    assert code == 0, err
    assert "T2: contest resolved" in out and "alpha" in out, out
