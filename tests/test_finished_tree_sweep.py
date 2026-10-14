"""A merged item's worktree is kept when merge runs inside it; later commands remove it
(B4757c428f4).

`merge` refuses to delete the directory its caller stands in, and said to run
`ddflow cleanup --apply` afterwards, but nothing retried: `complete` and the next session
start left the tree and its branch behind. `complete` from outside the tree disposes it, and
the `brief` sweeps the trees of DONE items finished longer ago than `worktree.sweep_min_age_s`
(a tree finished a moment ago may be the directory an agent still stands in).
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
from ddflow.services import cleanup as CL


def _seed(repo: Path, item: str) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": [f"src/{item}/*"]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    return Path(out.data["worktree"])


def _branches(repo: Path) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.split()


def _finish(repo: Path, item: str) -> None:
    """Done in the queue, tree and branch still there: what a merge from inside leaves."""
    LC.release(repo, item, agent="worker")
    EventLog(repo, "worker").append("item.completed", item, {"sha": "abc"})


def test_complete_from_outside_the_tree_disposes_it(repo):
    tree = _seed(repo, "T1")
    out = LC.complete(repo, "T1", force=True, agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["worktree_removed"] is True
    assert not tree.exists() and "ddflow/T1" not in _branches(repo)


def test_complete_from_inside_the_tree_keeps_it_and_says_how_it_goes_later(repo):
    tree = _seed(repo, "T2")
    out = LC.complete(repo, "T2", force=True, agent="worker", shell_cwd=tree)
    assert "standing in it" in out.data["kept_reason"] and "brief" in out.data["kept_reason"]
    assert tree.exists()


def test_a_session_start_sweeps_the_tree_of_an_item_finished_long_ago(repo):
    tree = _seed(repo, "T3")
    _finish(repo, "T3")
    cfg, log = Config.load(repo), EventLog(repo, "later")
    state = fold(log.read_all(), strict=False)
    later = time.time() + cfg.worktree.sweep_min_age_s + 60
    said = CL.sweep_finished(repo, cfg, log, state, now=later)
    assert said == ["removed the finished worktree of T3"]
    assert not tree.exists() and "ddflow/T3" not in _branches(repo)


def test_a_tree_finished_a_moment_ago_is_left_alone(repo):
    tree = _seed(repo, "T4")
    _finish(repo, "T4")
    cfg, log = Config.load(repo), EventLog(repo, "later")
    state = fold(log.read_all(), strict=False)
    assert CL.sweep_finished(repo, cfg, log, state) == []
    assert tree.exists()


def test_the_sweep_never_removes_a_tree_holding_work(repo):
    tree = _seed(repo, "T5")
    (tree / "draft.py").write_text("x = 1\n")
    _finish(repo, "T5")
    cfg, log = Config.load(repo), EventLog(repo, "later")
    state = fold(log.read_all(), strict=False)
    said = CL.sweep_finished(repo, cfg, log, state, now=time.time() + 10**6)
    assert tree.exists() and said and "uncommitted" in said[0]


def test_the_brief_runs_the_sweep(repo, monkeypatch):
    tree = _seed(repo, "T6")
    _finish(repo, "T6")
    assert run_cli(repo, "config", "worktree.sweep_min_age_s", "0")[0] == 0
    code, out, err = run_cli(repo, "brief")
    assert code == 0, err
    assert not tree.exists()


def test_the_knob_turns_the_sweep_off(repo):
    tree = _seed(repo, "T7")
    _finish(repo, "T7")
    assert run_cli(repo, "config", "worktree.sweep_min_age_s", "0")[0] == 0
    assert run_cli(repo, "config", "worktree.dispose_finished", "false")[0] == 0
    assert run_cli(repo, "brief")[0] == 0
    assert tree.exists()


def test_the_sweep_names_a_branch_it_could_not_delete(repo, monkeypatch):
    tree = _seed(repo, "T11")
    _finish(repo, "T11")
    cfg, log = Config.load(repo), EventLog(repo, "later")
    state = fold(log.read_all(), strict=False)
    monkeypatch.setattr(
        CL, "dispose_tree", lambda *a, **k: CL.Disposal("removed", branch_left="in use")
    )
    said = CL.sweep_finished(repo, cfg, log, state, now=time.time() + 10**6)
    assert said == ["removed the finished worktree of T11; branch ddflow/T11 left: in use"]
    assert tree.exists()  # the stub removed nothing
