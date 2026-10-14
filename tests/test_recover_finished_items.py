"""A finished item whose worktree is gone is not an orphan (B999669a8df).

`scan` reported every item with no lease and a recorded worktree, and finishing an item never
clears that record, so each done or abandoned item whose tree had been removed was listed on
every `doctor` and `brief` as "worktree is gone; nothing to salvage": 71 notes with nothing to
do. A finished item whose tree still exists is still reported (it may hold unmerged work).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services import leases as L


def _seed(repo: Path, item: str) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": [f"src/{item}/*"]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    path = Path(out.data["worktree"])
    assert LC.release(repo, item, agent="worker").exit == 0
    return path


def _scan(repo: Path) -> list[L.Recovery]:
    log = EventLog(repo, "scanner")
    return L.scan(log, Config.load(repo), repo)


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _drop_tree(repo: Path, path: Path) -> None:
    _git("-C", str(repo), "worktree", "remove", "--force", str(path))


def test_an_abandoned_item_whose_tree_is_gone_is_not_reported(repo):
    path = _seed(repo, "T1")
    # abandon now disposes a clean tree itself (B79319c6ada): keep it to test the scan
    assert run_cli(repo, "config", "worktree.dispose_finished", "false")[0] == 0
    assert LC.abandon(repo, "T1", reason="not needed", agent="worker").exit == 0
    assert any(r.item == "T1" for r in _scan(repo)), "its tree still exists: still reported"
    _drop_tree(repo, path)
    assert not [r for r in _scan(repo) if r.item == "T1"], (
        "gone tree, finished item: nothing to say"
    )


def test_a_done_item_whose_tree_is_gone_is_not_reported(repo):
    path = _seed(repo, "T2")
    EventLog(repo, "worker").append("item.completed", "T2", {"sha": "abc"})
    _drop_tree(repo, path)
    assert not [r for r in _scan(repo) if r.item == "T2"]


def test_an_unfinished_item_whose_tree_is_gone_is_still_reported(repo):
    """Only FINISHED items are quiet: an open item that lost its tree may have lost work."""
    path = _seed(repo, "T3")
    _drop_tree(repo, path)
    recs = [r for r in _scan(repo) if r.item == "T3"]
    assert recs and "gone" in recs[0].advice


def test_a_finished_item_whose_tree_holds_unmerged_commits_is_still_reported(repo):
    path = _seed(repo, "T4")
    (path / "work.py").write_text("print('only copy')\n")
    _git("-C", str(path), "add", "work.py")
    _git("-C", str(path), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "wip")
    assert LC.abandon(repo, "T4", reason="superseded", agent="worker").exit == 0
    recs = [r for r in _scan(repo) if r.item == "T4"]
    assert recs and recs[0].unmerged_commits == 1
