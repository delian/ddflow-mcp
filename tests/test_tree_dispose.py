"""Every path that removes a worktree records it, and onboarding never removes a tree an
agent holds (B5e83fb22cb).

`merge` and the flow log `worktree.removed`; `cleanup --apply` and `onboard` removed
trees without it, so the fold kept `item.worktree` pointing at a directory that was gone.
And onboarding removed with force, outside the log lock and with no look at the leases,
so a tree an agent had just claimed -- clean, nothing ahead of the base -- was deleted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import onboard as ON

OK = 0


def _tree_of(repo: Path, item: str) -> Path:
    code, shown, err = run_cli(repo, "--json", "show", item)
    assert code == OK, err
    tree = Path(json.loads(shown)["worktree"])
    tree = tree if tree.is_absolute() else (repo / tree).resolve()
    assert tree.exists(), "the fixture produced no worktree"
    return tree


def _released_tree(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    tree = _tree_of(repo, "T1")
    code, _, err = run_cli(repo, "release", "T1", agent="worker")
    assert code == OK, err
    return tree


def _recorded_tree(repo: Path) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree


def test_cleanup_apply_records_the_tree_it_removed(repo):
    tree = _released_tree(repo)
    api.cleanup(repo, apply=True, agent="sweeper")
    assert not tree.exists(), "fixture: cleanup should remove a released merged tree"
    assert _recorded_tree(repo) == "", "the fold still points at the removed tree"


def test_onboard_apply_records_the_tree_it_removed(repo):
    tree = _released_tree(repo)
    ON.apply(repo, [str(tree)])
    assert not tree.exists(), "fixture: onboard should remove a released merged tree"
    assert _recorded_tree(repo) == "", "the fold still points at the removed tree"


def test_onboard_apply_never_removes_a_tree_an_agent_holds(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    tree = _tree_of(repo, "T1")
    out = ON.apply(repo, [str(tree)])
    assert tree.exists(), f"onboard removed a live-leased tree: {out}"
    assert out[0]["outcome"] == "refused" and "worker" in out[0]["detail"], out


def test_onboard_apply_never_removes_a_tree_an_item_adopted(repo):
    """The harness's own working tree, bound to an item and released: no lease protects
    it, only adoption -- as `cleanup` already honours."""
    import subprocess

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    tree = repo.parent / "harness-tree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(tree), "-b", "agent-work"],
        check=True,
    )
    code, out_, err = run_cli(tree, "claim", "T1", agent="worker")
    assert code == OK and "adopted" in out_, out_ + err
    assert run_cli(repo, "release", "T1", agent="worker")[0] == OK
    out = ON.apply(repo, [str(tree)])
    assert tree.exists(), f"onboard removed an adopted tree: {out}"


def test_the_removal_is_written_as_the_agent_ddflow_agent_names(repo, monkeypatch):
    tree = _released_tree(repo)
    monkeypatch.setenv("DDFLOW_AGENT", "sweeper-7")
    ON.apply(repo, [str(tree)])
    removed = [e for e in EventLog(repo).read_all() if e.kind == "worktree.removed"]
    assert removed and removed[-1].agent == "sweeper-7", removed


def test_the_removal_is_written_as_the_declared_agent(repo):
    """`--agent` (or `ddflow_identify`) reaches the removal, as it reaches every write."""
    tree = _released_tree(repo)
    api.onboard_run(repo, stage="preflight", apply=True, accept=[str(tree)], agent="declared-3")
    removed = [e for e in EventLog(repo).read_all() if e.kind == "worktree.removed"]
    assert removed and removed[-1].agent == "declared-3", removed


# -- dispose_tree: the one removal (B-uni-tree-lifecycle.3-dispose) ----------------------------

import subprocess  # noqa: E402

from ddflow.config import Config  # noqa: E402
from ddflow.infra import worktree as W  # noqa: E402
from ddflow.services import cleanup as C  # noqa: E402


def _item(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def _wt(repo: Path, tree: Path) -> W.Worktree:
    return W.Worktree(item="T1", path=tree, branch=_item(repo).branch, base="main")


def _removed_events(repo: Path):
    return [e for e in EventLog(repo).read_all() if e.kind == "worktree.removed"]


def _commit_in(tree: Path, name: str = "extra.txt") -> None:
    (tree / name).write_text("x")
    subprocess.run(["git", "-C", str(tree), "add", name], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "work"], check=True)


def test_dispose_removes_a_clean_merged_tree_deletes_its_branch_and_logs_it(repo):
    tree = _released_tree(repo)
    wt = _wt(repo, tree)
    d = C.dispose_tree(repo, Config.load(repo), EventLog(repo, "t"), wt)
    assert d.removed and d.outcome == "removed" and d.had_branch and not d.branch_left, d
    assert not tree.exists() and not W.branch_exists(repo, wt.branch)
    assert [e.subject for e in _removed_events(repo)] == ["T1"]
    assert _recorded_tree(repo) == ""


def test_dispose_keeps_a_tree_a_live_lease_holds_but_not_the_callers_own(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1", agent="worker")[0] == OK
    tree = _tree_of(repo, "T1")
    wt, cfg, log = _wt(repo, tree), Config.load(repo), EventLog(repo, "t")
    d = C.dispose_tree(repo, cfg, log, wt)
    assert d.outcome == "refused" and d.kind == "held" and "worker" in d.why, d
    assert tree.exists() and _removed_events(repo) == []
    # the item being merged holds its own lease: that does not count against its tree
    d = C.dispose_tree(repo, cfg, log, wt, item=_item(repo))
    assert d.removed, d
    assert not tree.exists() and [e.subject for e in _removed_events(repo)] == ["T1"]


def test_dispose_leaves_work_alone_unless_the_caller_has_proof(repo):
    tree = _released_tree(repo)
    _commit_in(tree)
    (tree / "scratch.txt").write_text("uncommitted")
    wt, cfg, log = _wt(repo, tree), Config.load(repo), EventLog(repo, "t")
    d = C.dispose_tree(repo, cfg, log, wt)
    assert d.outcome == "failed" and "refusing to remove" in d.why, d
    assert tree.exists() and _removed_events(repo) == []
    forced = C.dispose_tree(repo, cfg, log, wt, forced_by="the test says so")
    assert forced.removed, forced
    assert not tree.exists() and len(_removed_events(repo)) == 1


def test_dispose_says_when_the_branch_could_not_be_deleted(repo):
    tree = _released_tree(repo)
    _commit_in(tree)  # an unmerged commit: `branch -d` refuses after a forced removal
    d = C.dispose_tree(
        repo, Config.load(repo), EventLog(repo, "t"), _wt(repo, tree), forced_by="test"
    )
    assert d.removed and d.had_branch and d.branch_left, d
    assert not tree.exists()


def test_dispose_without_a_log_removes_and_records_nothing(repo):
    tree = _released_tree(repo)
    d = C.dispose_tree(repo, Config.load(repo), None, _wt(repo, tree))
    assert d.removed and not tree.exists() and _removed_events(repo) == []


def _bound_names(tree) -> tuple[set[str], set[str]]:
    """(names bound to the worktree module, names bound to its `remove`) in one module."""
    import ast

    modules: set[str] = set()
    funcs: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                if mod.endswith("infra") and a.name == "worktree":
                    modules.add(a.asname or a.name)
                elif mod.endswith("infra.worktree") and a.name == "remove":
                    funcs.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            modules |= {a.asname or a.name for a in n.names if a.name.endswith("infra.worktree")}
    return modules, funcs


def _calls_remove(tree, modules: set[str], funcs: set[str]) -> bool:
    import ast

    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "remove" and ast.unparse(f.value) in modules:
            return True
        if isinstance(f, ast.Name) and f.id in funcs:
            return True
    return False


def _removal_callers(root: Path) -> list[str]:
    """The modules that CALL `infra.worktree.remove`, under whatever name they bound it:
    the module under an alias (`W`, `worktree`, ...) or the function imported by name."""
    import ast

    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "worktree.py":
            continue
        tree = ast.parse(path.read_text())
        if _calls_remove(tree, *_bound_names(tree)):
            found.append(str(path.relative_to(root)))
    return found


def test_nothing_but_dispose_removes_a_worktree():
    """Merge, the PR flow, cleanup and onboarding all end in `cleanup.dispose_tree`; a new
    caller of `worktree.remove` would be an unguarded, unlogged fifth path."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    assert _removal_callers(root) == ["services/cleanup.py"]


def test_the_removal_scan_sees_every_way_to_call_it(tmp_path):
    (tmp_path / "a.py").write_text("from ..infra import worktree as X\nX.remove(1)\n")
    (tmp_path / "b.py").write_text("from ..infra.worktree import remove as rm\nrm(1)\n")
    (tmp_path / "c.py").write_text("from ..infra.worktree import remove\nremove(1)\n")
    (tmp_path / "d.py").write_text("import ddflow.infra.worktree as w\nw.remove(1)\n")
    (tmp_path / "e.py").write_text("from ..infra import worktree as W\nW.list_worktrees(1)\n")
    (tmp_path / "f.py").write_text("items = []\nitems.remove(1)\n")
    assert _removal_callers(tmp_path) == ["a.py", "b.py", "c.py", "d.py"]
