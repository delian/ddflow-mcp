"""A lease held by ANOTHER clone is judged by when THIS clone saw it renewed, not by the
claimant's wall clock (B5a6651cd8e).

A lease's `at` is the claimant's `time.time()`. Compared with the observer's clock, a
claimant whose clock is 2 h behind made a LIVE claim look 5401 s expired ("EXPIRED -- take it
with --force", `recover`: "ddflow release T1") and one 2 h ahead made a dead claim last 9000 s.
Lamport order is unaffected; only liveness read wall time. For a lease of another clone the
renewal is now timed from the moment this clone first saw it (receipt time, kept in
`.ddflow/local/lease-seen`), so a skewed clock changes neither the verdict nor the time left.

The clocks are real: every command runs as a subprocess whose `time.time` is offset by a
`sitecustomize` (`DD_SKEW` seconds) -- no sleeping, and the two-clone setup is the production
path (`claim` writes the clone id).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import git_raw as _git

REPO_ROOT = Path(__file__).resolve().parents[1]

_SITE = (
    "import os, time as _t\n"
    "_off = float(os.environ.get('DD_SKEW', '0'))\n"
    "if _off:\n"
    "    _real = _t.time\n"
    "    _t.time = lambda: _real() + _off\n"
)

TTL = 1800
LATE = 3 * TTL  # far past TTL + any grace


@dataclass
class World:
    root: Path
    skew_dir: Path
    claimant_skew: float = 0.0

    def cli(self, who: str, agent: str, offset: float, *argv: str) -> tuple[int, str]:
        env = {
            **os.environ,
            "PYTHONPATH": f"{self.skew_dir}{os.pathsep}{REPO_ROOT}",
            "DD_SKEW": str(offset),
        }
        env.pop("DDFLOW_AGENT", None)
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "ddflow",
                "--repo",
                str(self.root / who),
                "--agent",
                agent,
                *argv,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
        return p.returncode, p.stdout + p.stderr

    def sync(self, src: str, dst: str) -> None:
        _git(self.root / src, "add", "-A", ".ddflow/events")
        _git(self.root / src, "commit", "-qm", "sync", "--no-verify")
        _git(self.root / src, "push", "-q", "origin", "main")
        _git(self.root / dst, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")


def _clone(remote: Path, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)


@pytest.fixture
def world(tmp_path: Path) -> World:
    skew = tmp_path / "skew"
    skew.mkdir()
    (skew / "sitecustomize.py").write_text(_SITE)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _clone(remote, tmp_path / "alice")
    a = tmp_path / "alice"
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    w = World(tmp_path, skew)
    for t in ("T1", "T2"):
        assert (
            w.cli("alice", "alice", 0, "task", "add", t, "--title", t, "--globs", f"src/{t}.py")[0]
            == 0
        )
    _git(a, "add", "-A", ".ddflow")
    _git(a, "commit", "-qm", "tasks", "--no-verify")
    _git(a, "push", "-q", "origin", "main")
    _clone(remote, tmp_path / "bob")
    return w


def _left(out: str) -> int:
    m = re.search(r"lease \S+ \((-?\d+)s left\)", out)
    if m:
        return int(m.group(1))
    # a lapsed lease reads "ran out Ns ago" (never a negative countdown), so it is -N here
    m = re.search(r"lease \S+ \(ran out (\d+)s ago", out)
    assert m, out
    return -int(m.group(1))


@pytest.fixture(params=[-7200, 7200], ids=["claimant-2h-behind", "claimant-2h-ahead"])
def seen(world: World, request) -> World:
    """alice claims T1 on a skewed clock; bob (true clock) has just pulled it."""
    world.claimant_skew = request.param
    assert world.cli("alice", "alice", request.param, "claim", "T1")[0] == 0
    world.sync("alice", "bob")
    return world


def test_a_skewed_claimant_clock_does_not_change_a_live_lease(seen: World):
    w = seen
    code, out = w.cli("bob", "bob", 0, "show", "T1")
    assert code == 0 and abs(_left(out) - TTL) < 120, out
    _code, out = w.cli("bob", "bob", 0, "status")
    assert "INTERRUPTED" not in out, out
    _code, out = w.cli("bob", "bob", 0, "next")
    assert "INTERRUPTED" not in out, out
    for argv in (("claim", "T1"), ("claim", "T1", "--force")):
        code, out = w.cli("bob", "bob", 0, *argv)
        assert code == 3 and "EXPIRED" not in out and "held by alice" in out, (argv, code, out)
    _code, out = w.cli("bob", "bob", 0, "recover")
    assert "ddflow release" not in out, out
    assert "Nothing to recover" in out, out
    _code, out = w.cli("bob", "bob", 0, "cleanup")
    assert "expired" not in out.lower(), out
    code, out = w.cli("bob", "bob", 0, "heartbeat", "T1")
    assert code == 2 and "expired" not in out.lower(), out  # not bob's lease; not "lapsed" either


def test_a_dead_foreign_lease_still_lapses_by_receipt_time(seen: World):
    w = seen
    assert "INTERRUPTED" not in w.cli("bob", "bob", 0, "status")[1]  # first sight: receipt time
    _code, out = w.cli("bob", "bob", LATE, "status")
    assert "INTERRUPTED" in out, out
    code, out = w.cli("bob", "bob", LATE, "claim", "T1")
    assert code == 3 and "EXPIRED" in out, out


def test_a_renewal_seen_later_extends_it_from_when_it_was_seen(seen: World):
    w = seen
    assert w.cli("bob", "bob", 0, "status")[0] == 0  # receipt of the claim
    # alice renews on her skewed clock; bob pulls it near the end of the first TTL
    assert w.cli("alice", "alice", w.claimant_skew + TTL - 100, "heartbeat", "T1")[0] == 0
    w.sync("alice", "bob")
    _code, out = w.cli("bob", "bob", TTL - 100, "show", "T1")  # bob's clock: 100 s before expiry
    assert abs(_left(out) - TTL) < 150, out  # the renewal was first seen NOW, so ~a full TTL
    _code, out = w.cli("bob", "bob", TTL - 100 + LATE, "show", "T1")
    assert _left(out) < 0, out


def test_the_observers_own_lease_is_judged_by_its_own_clock_without_a_ledger_entry(world: World):
    w = world
    assert w.cli("bob", "bob", 0, "claim", "T2")[0] == 0
    _code, out = w.cli("bob", "bob", 0, "show", "T2")
    assert abs(_left(out) - TTL) < 120, out
    seen_file = w.root / "bob" / ".ddflow" / "local" / "lease-seen"
    if seen_file.exists():
        assert "T2" not in seen_file.read_text() and '"lease:' not in seen_file.read_text()
        assert json.loads(seen_file.read_text()).get("data", {}) == {}
    _code, out = w.cli("bob", "bob", LATE, "show", "T2")
    assert _left(out) < 0, out


def test_recover_names_the_other_clone_not_release(seen: World):
    w = seen
    # make the lease look lapsed to the observer so recover has something to say
    assert w.cli("bob", "bob", 0, "status")[0] == 0
    _code, out = w.cli("bob", "bob", LATE, "recover")
    assert "ddflow release T1" not in out, out
    assert "worktree is in another clone" in out and "alice" in out and "ask its owner" in out, out


def test_deleting_the_ledger_restarts_the_ttl_and_never_expires_a_live_lease(seen: World):
    w = seen
    assert w.cli("bob", "bob", 0, "status")[0] == 0
    ledger = w.root / "bob" / ".ddflow" / "local" / "lease-seen"
    assert ledger.exists()
    ledger.unlink()
    _code, out = w.cli("bob", "bob", 600, "show", "T1")
    assert abs(_left(out) - TTL) < 150, out  # restarted from this read, not shortened
    assert "INTERRUPTED" not in w.cli("bob", "bob", 600, "status")[1]


def test_a_hand_edited_ledger_row_is_read_as_fresh_not_a_crash(seen: World):
    w = seen
    assert w.cli("bob", "bob", 0, "status")[0] == 0
    ledger = w.root / "bob" / ".ddflow" / "local" / "lease-seen"
    doc = json.loads(ledger.read_text())
    assert doc["data"], doc
    doc["data"] = {k: {"renewed_at": "x", "seen": 1} for k in doc["data"]}
    ledger.write_text(json.dumps(doc))
    code, out = w.cli("bob", "bob", 0, "show", "T1")
    assert code == 0 and abs(_left(out) - TTL) < 150, out
