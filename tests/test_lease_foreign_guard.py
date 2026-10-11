"""A live lease of ANOTHER clone is not released, and its gate outcomes not overwritten,
without --force and a reason (B90b43dd2d8).

`release` and `gate run|record|skip` used to act on any item whatever lease it carried:
bob's `release T2` dropped alice's live lease (her next heartbeat said "no lease held"),
and bob's `gate run T1 unit_tests` wrote gate.unavailable over alice's recorded pass in
every clone (last event wins). A lease is evidence of intent, not a lock, so the override
stays possible -- but it needs `--force` with a reason and is recorded.

The guard is per CLONE (operator decision D-mu-bugfix-answers): agents of the same clone,
the holder itself, an expired lease and a lease with no recorded clone are unchanged.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from helpers import git_raw as _git

from ddflow.config import Config
from ddflow.infra import log as L
from ddflow.infra.log import EventLog
from ddflow.services import leases as LS
from tests.conftest import run_cli

REFUSED = 3


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    monkeypatch.setattr(L.EventLog, "stamp", False)


# -- the predicate, on a hand-built log -----------------------------------------------------


def _log(tmp_path: Path, clone: str) -> EventLog:
    repo = tmp_path / "r"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    log = EventLog(repo, "bob")
    (repo / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
    (repo / ".ddflow" / "local" / "clone-id").write_text(clone + "\n")
    return log


def _lease(log: EventLog, *, clone: str | None, at: float, ttl: int = 1000) -> None:
    log.append("task.added", "T", {"title": "t"})
    extra = {} if clone is None else {"clone": clone}
    log.append("lease.acquired", "T", {"holder": "U9", "at": at, "ttl_s": ttl, **extra})


def test_another_clones_live_lease_is_foreign(tmp_path):
    log = _log(tmp_path, "bbbbbb")
    _lease(log, clone="aaaaaa", at=time.time())
    held = LS.foreign_live_lease(log, Config(), "T", "bob")
    assert held is not None and held.holder == "U9"


def test_the_same_clones_lease_is_not_foreign_whatever_the_agent(tmp_path):
    log = _log(tmp_path, "aaaaaa")
    _lease(log, clone="aaaaaa", at=time.time())
    assert LS.foreign_live_lease(log, Config(), "T", "bob") is None


def test_a_lease_with_no_recorded_clone_is_not_foreign(tmp_path):
    log = _log(tmp_path, "bbbbbb")
    _lease(log, clone=None, at=time.time())
    assert LS.foreign_live_lease(log, Config(), "T", "bob") is None


def test_an_expired_foreign_lease_is_not_foreign_live(tmp_path):
    log = _log(tmp_path, "bbbbbb")
    _lease(log, clone="aaaaaa", at=time.time() - 100000, ttl=10)
    cfg = Config()
    cfg.lease.grace_s = 0
    # the first sight of a foreign lease is timed from OUR clock (B5a6651cd8e), so make
    # the ledger remember it from long ago
    from ddflow.core.model import fold
    from ddflow.services.leaseclock import localize

    st = fold(log.read_all(), strict=False)
    localize(st, log, time.time() - 100000)
    assert LS.foreign_live_lease(log, cfg, "T", "bob") is None


# -- end to end: two real clones --------------------------------------------------------------


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _events(repo: Path) -> list[dict]:
    out = []
    for shard in sorted((repo / ".ddflow" / "events").glob("*.jsonl")):
        out += [json.loads(ln) for ln in shard.read_text().splitlines() if ln.strip()]
    return out


@pytest.fixture
def clones(tmp_path: Path):
    """alice (U9, clone a) holds T1 and T2; bob (U2, clone b) has pulled both."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    for t in ("T1", "T2"):
        assert (
            run_cli(a, "task", "add", t, "--title", t, "--globs", f"src/{t}.py", agent="U9")[0] == 0
        )
    _commit_log(a, "tasks")
    _git(a, "push", "-q", "origin", "main")
    for t in ("T1", "T2"):
        assert run_cli(a, "claim", t, "--no-worktree", agent="U9")[0] == 0
    _commit_log(a, "claims")
    _git(a, "push", "-q", "origin", "main")
    b = _clone(remote, tmp_path / "b")
    # bob's own command for the gate, as a real user's machine has one (s9 of the repro)
    cmd = "python -c 'print(1)'"
    assert run_cli(b, "config", "--local", "--set", "gate.unit_tests.command", cmd)[0] == 0
    return a, b


def _lease_of(repo: Path, item: str = "T1"):
    from ddflow.core.model import fold

    return fold(EventLog(repo, "probe").read_all(), strict=False).items[item].lease


def test_release_of_another_clones_live_lease_is_refused(clones):
    _a, b = clones
    before = _events(b)
    code, out, err = run_cli(b, "release", "T2", agent="U2")
    assert code == REFUSED, out + err
    assert "U9" in out + err and "--force" in out + err
    assert _events(b) == before  # no event appended
    assert _lease_of(b, "T2") is not None


def test_release_force_needs_a_reason(clones):
    _a, b = clones
    before = _events(b)
    code, out, err = run_cli(b, "release", "T2", "--force", agent="U2")
    assert code == REFUSED and "reason" in (out + err).lower(), out + err
    assert _events(b) == before


def test_release_force_with_a_reason_releases_and_records_it(clones):
    _a, b = clones
    code, out, err = run_cli(b, "release", "T2", "--force", "--reason", "U9 is gone", agent="U2")
    assert code == 0, out + err
    ev = [e for e in _events(b) if e["kind"] == "lease.released"][-1]
    assert ev["data"]["forced"] is True
    assert "U9 is gone" in ev["data"]["note"]
    assert _lease_of(b, "T2") is None


def test_the_holder_and_its_own_clone_release_without_force(clones):
    a, _b = clones
    # another agent of alice's clone (a coordinator clearing a crashed sub-agent)
    code, out, err = run_cli(a, "release", "T1", agent="coordinator")
    assert code == 0, out + err
    code, out, err = run_cli(a, "release", "T2", agent="U9")
    assert code == 0, out + err


@pytest.mark.parametrize(
    "argv",
    [
        ("gate", "run", "T1", "unit_tests"),
        ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "e"),
        ("gate", "skip", "T1", "research", "--reason", "n/a here"),
    ],
)
def test_gate_writes_on_another_clones_live_lease_are_refused(clones, argv):
    _a, b = clones
    before = _events(b)
    code, out, err = run_cli(b, *argv, agent="U2")
    assert code == REFUSED, out + err
    assert "U9" in out + err and "--force" in out + err
    assert _events(b) == before


@pytest.mark.parametrize(
    "argv",
    [
        ("gate", "run", "T1", "unit_tests"),
        ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "e"),
        ("gate", "skip", "T1", "research"),
    ],
)
def test_gate_force_without_a_reason_is_refused(clones, argv):
    _a, b = clones
    before = _events(b)
    assert run_cli(b, *argv, "--force", agent="U2")[0] == REFUSED
    assert _events(b) == before


@pytest.mark.parametrize(
    "argv",
    [
        ("gate", "run", "T1", "unit_tests"),
        ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "e"),
        ("gate", "skip", "T1", "research"),
    ],
)
def test_gate_force_with_a_reason_writes_and_marks_the_event(clones, argv):
    _a, b = clones
    before = {e["id"] for e in _events(b)}
    code, out, err = run_cli(b, *argv, "--force", "--reason", "alice is away", agent="U2")
    assert code in (0, 2), out + err
    new = [e for e in _events(b) if e["id"] not in before and e["kind"].startswith("gate.")]
    written = [e for e in new if e["kind"] != "gate.started"]
    assert written and all(e["data"].get("forced") is True for e in written), new


def test_the_holders_own_clone_writes_gates_without_force(clones):
    a, _b = clones
    code, out, err = run_cli(
        a,
        "gate",
        "record",
        "T1",
        "research",
        "--outcome",
        "passed",
        "--evidence",
        "e",
        agent="coordinator",
    )
    assert code == 0, out + err
    assert "forced" not in json.dumps(_events(a)[-1])


def test_a_refused_gate_run_leaves_the_holders_recorded_pass_in_both_clones(clones):
    a, b = clones
    assert (
        run_cli(
            a,
            "gate",
            "record",
            "T1",
            "research",
            "--outcome",
            "passed",
            "--evidence",
            "ok",
            agent="U9",
        )[0]
        == 0
    )
    _commit_log(a, "alice research")
    _git(a, "push", "-q", "origin", "main")
    _git(b, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    assert run_cli(b, "gate", "run", "T1", "research", agent="U2")[0] != 0
    assert run_cli(b, "gate", "run", "T1", "unit_tests", agent="U2")[0] == REFUSED
    for repo, who in ((a, "U9"), (b, "U2")):
        code, out, err = run_cli(repo, "--json", "gate", "status", "T1", agent=who)
        assert code == 0, out + err
        status = json.loads(out)
        assert "research" in status["done"], status
        assert "research" not in status["unavailable"], status
