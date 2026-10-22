"""A landed item whose crash left it abandoned: cleanup retires its tree (Bfc4ac64c9c).

The merge gate recorded the landing (`merged_sha`, reachable from main); what the tree
still has ahead of main is a merge-main commit. `survey` called that "unmerged ... finish
it" with no safe action, because it never consulted the recorded merge.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import cleanup as CL


def _g(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _landed_then_abandoned(repo: Path, tmp_path: Path) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "T1", "globs": ["f.txt"]})
    out = LC.claim(repo, "T1", agent="worker")
    assert out.exit == 0, out.reason
    tree = Path(out.data["worktree"])
    (tree / "f.txt").write_text("work\n")
    _g(tree, "add", "f.txt")
    _g(tree, "commit", "-qm", "work")
    _g(repo, "merge", "-q", "--no-ff", "-m", "land", out.data["branch"])
    log.append("worktree.merged", "T1", {"sha": _g(repo, "rev-parse", "HEAD")})
    (repo / "later.txt").write_text("x\n")
    _g(repo, "add", "later.txt")
    _g(repo, "commit", "-qm", "main moves on")
    _g(tree, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    LC.release(repo, "T1", agent="worker")
    EventLog(repo, "worker").append("item.abandoned", "T1", {"reason": "crash"})
    return tree


def _plan(repo: Path):
    state = fold(EventLog(repo).read_all(), strict=False)
    return CL.survey(repo, Config.load(repo), state)


def test_landed_item_with_only_a_merge_main_commit_is_safe_to_remove(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    t = next(x for x in _plan(repo).trees if x.path == str(tree))
    assert t.ahead, "fixture: the tree should be ahead of main by the merge commit"
    assert t.action == "remove", t.done
    assert t.kind == "merged"


def test_landed_item_with_real_later_work_is_not_offered_for_removal(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    (tree / "new.txt").write_text("unlanded\n")
    _g(tree, "add", "new.txt")
    _g(tree, "commit", "-qm", "after landing")
    t = next(x for x in _plan(repo).trees if x.path == str(tree))
    assert t.action == "", t.done
    assert t.kind == "unmerged"


def test_apply_removes_tree_and_branch_of_a_landed_item(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    cfg = Config.load(repo)
    done = CL.apply(repo, cfg, _plan(repo), EventLog(repo, "sweeper"))
    assert not tree.exists(), done
    assert "ddflow/T1" not in _g(repo, "branch", "--format=%(refname:short)").split()


def test_a_merge_commit_carrying_its_own_edit_is_not_a_leftover(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    _g(repo, "commit", "-q", "--allow-empty", "-m", "main again")
    _g(tree, "merge", "-q", "--no-ff", "--no-commit", "main")
    (tree / "evil.txt").write_text("only in the merge\n")
    _g(tree, "add", "evil.txt")
    _g(tree, "commit", "-qm", "merge main, with an edit")
    t = next(x for x in _plan(repo).trees if x.path == str(tree))
    assert t.action == "", t.done


def test_a_root_commit_with_real_files_is_not_a_leftover(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    _g(tree, "checkout", "-q", "--orphan", "fresh")
    (tree / "only.txt").write_text("the only copy\n")
    _g(tree, "add", "only.txt")
    _g(tree, "commit", "-qm", "root")
    _g(tree, "branch", "-M", "ddflow/T1")
    t = next(x for x in _plan(repo).trees if x.path == str(tree))
    assert t.action == "", t.done


def test_a_file_named_like_two_pins_is_not_a_pin(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    (tree / "a.pin b.pin").write_text("x\n")
    _g(tree, "add", "a.pin b.pin")
    _g(tree, "commit", "-qm", "odd name")
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert not CL.landed_leftovers_only(str(tree), item, "main", ["a.pin", "b.pin"])


def test_a_tree_that_moved_after_the_proof_is_kept(repo, tmp_path, monkeypatch):
    tree = _landed_then_abandoned(repo, tmp_path)
    plan = _plan(repo)
    # the proof was of another commit than the tree now stands on
    monkeypatch.setattr(CL, "proved_leftover_tip", lambda *a, **k: "0" * 40)
    done = CL.apply(repo, Config.load(repo), plan, EventLog(repo, "sweeper"))
    assert tree.exists(), done
    assert "ddflow/T1" in _g(repo, "branch", "--format=%(refname:short)").split()


def test_landed_tip_still_refuses_a_file_written_after_the_proof(repo, tmp_path):
    from ddflow.infra import worktree as W

    tree = _landed_then_abandoned(repo, tmp_path)
    (tree / "scratch.txt").write_text("written after the check\n")
    wt = W.Worktree(item="T1", path=tree, branch="ddflow/T1", base="main")
    r = W.remove(repo, Config.load(repo), wt, landed_tip=_g(tree, "rev-parse", "HEAD"))
    assert not r.ok and tree.exists()


def test_landed_tip_refuses_when_a_commit_arrived_after_the_proof(repo, tmp_path):
    from ddflow.infra import worktree as W

    tree = _landed_then_abandoned(repo, tmp_path)
    proved = _g(tree, "rev-parse", "HEAD")
    (tree / "late.txt").write_text("committed after the proof\n")
    _g(tree, "add", "late.txt")
    _g(tree, "commit", "-qm", "late work")
    wt = W.Worktree(item="T1", path=tree, branch="ddflow/T1", base="main")
    r = W.remove(repo, Config.load(repo), wt, landed_tip=proved)
    assert not r.ok and tree.exists()


def test_files_under_a_pin_directory_are_pins(repo, tmp_path):
    tree = _landed_then_abandoned(repo, tmp_path)
    (tree / "snaps").mkdir()
    (tree / "snaps" / "x.ambr").write_text("generated\n")
    _g(tree, "add", "snaps")
    _g(tree, "commit", "-qm", "regenerate pins")
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert CL.landed_leftovers_only(str(tree), item, "main", ["snaps"])
    assert not CL.landed_leftovers_only(str(tree), item, "main", ["snaps2"])
