"""`complete --model` declared once is remembered past a refusal, and `recover` omits a
worktree a merge already removed (B-recover-forgets-author-model).

``close-blb87`` ran ``complete --model claude-opus-5-5`` and the completion was refused;
nothing kept the model, so the later lease sweep and ``recover`` reported "the author's
model is unknown" and ``lanes`` showed none for the lane. The same run also printed
``worktree .ddflow/worktrees/B95-false-limit`` a merge had removed. The model declaration
must be durably recorded, and a removed worktree must be left out (cleared), not named.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import leases as L

DECLARED = "claude-opus-5-5"


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _merged(repo: Path, item: str, rel: str) -> None:
    """``item`` claimed, one file committed on its branch, merged by ddflow with the lease
    left in place so it can lapse later (`lease.release_at_merge = false`)."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": [rel]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    wt = Path(out.data["worktree"])
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text("x = 1\n")
    _git("-C", str(wt), "add", "-A")
    ident = ["-c", "user.email=t@e.com", "-c", "user.name=T"]
    _git("-C", str(wt), *ident, "commit", "-qm", f"{item} work")
    run_cli(repo, "config", "--set", "lease.release_at_merge", "false")
    assert LC.merge(repo, item, agent="worker").exit == 0


def _recs(repo: Path, item: str, *, later: float = 10_000.0) -> list[L.Recovery]:
    return [
        r
        for r in L.scan(EventLog(repo, "scanner"), Config.load(repo), repo, now=time.time() + later)
        if r.item == item
    ]


def test_a_refused_complete_with_model_remembers_the_author_model(repo):
    """`complete --model X` is refused for a merged item, and X is still the author model a
    later `recover`/`lanes` reads -- not "unknown"."""
    _merged(repo, "T1", "src/T1/a.py")
    out = LC.complete(repo, "T1", agent="worker", model=DECLARED)
    assert out.exit != 0, "the completion must be refused (gates not passed)"
    log = EventLog(repo, "scanner")
    state = fold(log.read_all(), strict=False)
    assert L.open_session_model(state, "worker") == DECLARED
    (rec,) = _recs(repo, "T1")
    assert "author's model is unknown" not in rec.advice, rec.advice


def test_recover_omits_a_worktree_a_merge_removed(repo):
    """The merge removed the item's worktree; `recover` leaves its path out instead of
    naming a tree that no longer exists."""
    _merged(repo, "T1", "src/T1/a.py")
    (rec,) = _recs(repo, "T1")
    assert rec.kind == "expired_lease"
    assert rec.worktree == "", rec.worktree
