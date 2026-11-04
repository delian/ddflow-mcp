"""PS-git-batch (R-scalability): `status` with many stale leases spawns a bounded number of
git processes, and `brief` leaves the finished-worktree sweep to its cadence.

Before: the recovery scan measured every expired lease with its own `git status`, `rev-list`,
`merge-base` and `symbolic-ref` calls (622 subprocesses for 126 leases). After: one
`worktree list`, one `for-each-ref` and a per-HEAD cache keep it flat in the lease count.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.api import reporting as RP
from ddflow.infra.log import EventLog

STALE = 126
#: The falsifier (PS-git-batch): more than this many subprocesses for `status` fails.
MAX_SPAWNS = 30


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    return r.stdout.strip()


def _seed_stale(repo: Path, n: int = STALE) -> None:
    """``n`` merged items whose lease lapsed and whose clean tree is still on disk -- the
    shape of the measured log (622 subprocesses for 126 of them)."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    for i in range(n):
        t = f"T{i}"
        wt = repo.parent / "wts" / t
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"{t} work")
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "worktree", "add", "-q", "-b", f"ddflow/{t}", str(wt), sha)
        log.append("task.added", t, {"parent": "P1", "title": t, "globs": [f"src/{t}.py"]})
        log.append("item.started", t, {})
        lease = {
            "holder": f"agent{i}",
            "at": 1000.0,
            "ttl_s": 60,
            "worktree": str(wt),
            "branch": f"ddflow/{t}",
            "globs": [f"src/{t}.py"],
        }
        log.append("lease.acquired", t, lease)
        log.append("worktree.merged", t, {"branch": f"ddflow/{t}", "sha": sha})
        log.append("gate.passed", t, {"gate": "merge", "by": "x", "evidence": {"sha": sha}})


class _Spawns:
    def __init__(self, monkeypatch):
        self.argv: list = []
        popen = subprocess.Popen  # `subprocess.run` starts a Popen: count each start once
        outer = self

        class CountingPopen(popen):
            def __init__(self, *a, **k):
                outer.argv.append(a[0] if a else k.get("args"))
                super().__init__(*a, **k)

        monkeypatch.setattr(subprocess, "Popen", CountingPopen)


def test_status_with_126_stale_leases_spawns_few_processes(repo, monkeypatch):
    _seed_stale(repo)
    spawns = _Spawns(monkeypatch)
    out = RP.status(repo, agent="reader")
    assert out.exit == 0, out.reason
    assert len(spawns.argv) <= MAX_SPAWNS, (
        f"{len(spawns.argv)} subprocesses for {STALE} stale leases: {sorted({' '.join(a[3:5]) if a[0]=='git' else a[0] for a in spawns.argv})} "
        + str([' '.join(a[3:] if a[1]=='-C' else a) for a in spawns.argv])
    )


def test_brief_sweeps_finished_worktrees_at_most_once_per_interval(repo, monkeypatch):
    run_cli(repo, "init")
    calls: list = []
    from ddflow.services import cleanup as CL

    monkeypatch.setattr(CL, "sweep_finished", lambda *a, **k: calls.append(1) or [])
    assert LC.brief(repo, agent="reader").exit == 0
    assert LC.brief(repo, agent="reader").exit == 0
    assert len(calls) == 1, "the second brief inside the interval must not sweep again"


def test_sweep_interval_zero_sweeps_on_every_brief(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "worktree.sweep_interval_s", "0")
    calls: list = []
    from ddflow.services import cleanup as CL

    monkeypatch.setattr(CL, "sweep_finished", lambda *a, **k: calls.append(1) or [])
    assert LC.brief(repo, agent="reader").exit == 0
    assert LC.brief(repo, agent="reader").exit == 0
    assert len(calls) == 2


def test_recover_still_measures_every_tree(repo):
    """The cap is for read-only views; `recover` weighs all of them."""
    from ddflow.config import Config
    from ddflow.services import leases as L

    _seed_stale(repo, 20)
    found = L.scan(EventLog(repo, "r"), Config.load(repo), repo)
    assert len(found) == 20
    assert all(r.salvageable is False for r in found), [r.advice for r in found[:2]]
    capped = L.scan(EventLog(repo, "r"), Config.load(repo), repo, view=True)
    assert sum(1 for r in capped if r.salvageable is None) == 20 - 16
    assert all("complete" in r.advice or "NOT MEASURED" in r.advice for r in capped)


def test_an_unknown_commit_is_not_reported_as_off_the_base(repo):
    """`merge-base --is-ancestor` exits 128 for a commit git does not know: that is
    "could not tell", not "not an ancestor", and it is not remembered."""
    from ddflow.infra.gitbatch import RepoFacts

    f = RepoFacts(repo)
    assert f.on_base("0" * 40, "main") is False
    assert ("0" * 40, "main") not in f._on_base


def test_one_query_tells_merged_commits_from_unmerged_ones(repo):
    """`rev-list --no-walk S... ^base` prints only the listed commits the base lacks, so a
    commit that IS an ancestor of the base is remembered as on it (and is one process)."""
    from ddflow.infra.gitbatch import RepoFacts

    on = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "side")
    off = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
    f = RepoFacts(repo)
    f.prefetch_on_base([on, off], "main")
    assert f._on_base == {(on, "main"): True, (off, "main"): False}
