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
