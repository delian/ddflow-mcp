"""The commit hook applies `update --globs`'s overlap rules (Bb7df2561a1).

`update` grants a glob over a wildcard another lease holds when its holder has changed
nothing there, so the hook has to refuse the holder who later changes THAT file: the
second claimer's live lease covers it. And a finished item's unreleased lease holds
nothing, for the hook as for `update`.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_hook_lapsed_lease import _setup

from ddflow.infra.log import EventLog
from ddflow.services import enforce as E


def _other_holds(repo: Path, glob: str, *, finished: bool = False) -> None:
    assert run_cli(repo, "task", "add", "T2", "--globs", glob)[0] == 0
    other = EventLog(repo, "other")
    other.append(
        "lease.acquired",
        "T2",
        {"holder": "other", "at": time.time(), "ttl_s": 1800, "globs": [glob]},
    )
    other.append("item.started", "T2", {})
    if finished:
        other.append("item.abandoned", "T2", {"reason": "done with it"})


def test_a_wildcard_holder_may_not_change_a_file_another_live_lease_names(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)  # owner holds src/*, and src/new.py is staged
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1, msg
    assert "ANOTHER agent" in msg and "T2 (other)" in msg, msg


def test_a_file_only_the_holder_covers_is_still_allowed(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "docs/elsewhere.md")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_a_finished_items_unreleased_lease_holds_nothing(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "src/new.py", finished=True)
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_a_finished_items_lease_is_not_named_as_the_holder_of_an_uncovered_path(
    repo, cfg, monkeypatch
):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "lib/x.py", finished=True)
    (tree / "lib").mkdir()
    (tree / "lib" / "x.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(tree), "add", "lib/x.py"], check=True)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1, msg
    assert "ANOTHER agent" not in msg and "T2 (other)" not in msg, msg


def _other_holds_wild(repo: Path, cfg, glob: str, *, changed: str | None) -> None:
    """T2 held by "other" on a wildcard, in a tree of its own that changed ``changed``."""
    from ddflow.infra import worktree as W

    assert run_cli(repo, "task", "add", "T2", "--globs", glob)[0] == 0
    wt = W.create(repo, cfg, "T2")
    EventLog(repo, "other").append(
        "lease.acquired",
        "T2",
        {
            "holder": "other",
            "at": time.time(),
            "ttl_s": 1800,
            "globs": [glob],
            "worktree": W.store_path(repo, wt.path),
            "branch": wt.branch,
        },
    )
    if changed:
        (wt.path / changed).parent.mkdir(parents=True, exist_ok=True)
        (wt.path / changed).write_text("z = 1\n")
        subprocess.run(["git", "-C", str(wt.path), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(wt.path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "c",
            ],
            check=True,
        )


def test_a_wildcard_holder_who_changed_the_file_holds_it(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds_wild(repo, cfg, "src/n*", changed="src/new.py")
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg


def test_a_wildcard_holder_who_never_touched_the_file_does_not(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds_wild(repo, cfg, "src/n*", changed="docs/readme.md")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_the_holders_own_finished_lease_covers_nothing_either(repo, cfg, monkeypatch):
    """A finished item holds nothing -- its holder's new work needs a lease of its own, as
    for `update`."""
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    EventLog(repo, "owner").append("item.abandoned", "T1", {"reason": "done"})
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "not covered" in msg, msg


def test_a_lease_on_another_release_line_is_another_branch(repo, cfg, monkeypatch):
    """`update` skips a lease on another line, so the hook must."""
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T2", "--globs", "src/new.py")[0] == 0
    other = EventLog(repo, "other")
    other.append("task.updated", "T2", {"line": "1"})
    other.append(
        "lease.acquired",
        "T2",
        {"holder": "other", "at": time.time(), "ttl_s": 1800, "globs": ["src/new.py"]},
    )
    other.append("item.started", "T2", {})
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_the_same_line_by_name_and_by_default_is_one_branch(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner")[0] == 1


def _lined_other(repo: Path, glob: str) -> None:
    assert run_cli(repo, "task", "add", "T2", "--globs", glob)[0] == 0
    other = EventLog(repo, "other")
    other.append("task.updated", "T2", {"line": "1"})
    other.append(
        "lease.acquired",
        "T2",
        {"holder": "other", "at": time.time(), "ttl_s": 1800, "globs": [glob]},
    )
    other.append("item.started", "T2", {})


def test_only_the_lines_of_the_leases_covering_the_path_count(repo, cfg, monkeypatch):
    """The committer also holds a line-1 lease, but on other files: it does not make the
    other agent's line-1 lease on src/new.py a rival of this commit."""
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "docs/*")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["docs/*"]},
    )
    _lined_other(repo, "src/new.py")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_the_commits_own_tree_decides_which_item_it_is_for(repo, cfg, monkeypatch):
    """Two items of the committer cover src/new.py, on lines "" and "1"; the tree being
    committed in is the line-"" item's, so a line-1 rival is another branch."""
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "src/*")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["src/*"]},
    )
    _lined_other(repo, "src/new.py")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_an_exact_lease_of_the_committers_other_item_does_not_hide_the_contest(
    repo, cfg, monkeypatch
):
    """The committer's line-"1" item names src/new.py exactly, but this commit is for the
    tree's own wildcard item on the default line, where another agent names it exactly too."""
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "src/new.py")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["src/new.py"]},
    )
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg


def test_outside_any_linked_tree_a_treeless_lease_is_not_the_commits_own(repo, cfg, monkeypatch):
    """`here` None must not equal a lease's missing tree."""
    _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "new.py").write_text("q = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/new.py"], check=True)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg


def test_a_path_two_of_the_committers_lines_cover_is_contested_by_a_rival_on_either(
    repo, cfg, monkeypatch
):
    """Outside any linked tree nothing says which item the commit is for."""
    _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "src/*")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["src/*"]},
    )
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "new.py").write_text("w = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/new.py"], check=True)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg


def test_an_exact_lease_on_one_line_does_not_hide_a_wildcard_on_the_other_outside_a_tree(
    repo, cfg, monkeypatch
):
    """Outside any tree the commit may be for either item: the line-"1" exact lease clears
    nothing for the default-line wildcard item, whose rival (default line) still contests."""
    _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "src/new.py")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["src/new.py"]},
    )
    _other_holds(repo, "src/new.py")
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "new.py").write_text("v = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/new.py"], check=True)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg


def test_a_line_one_rival_contests_the_path_too(repo, cfg, monkeypatch):
    _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    assert run_cli(repo, "task", "add", "T3", "--globs", "src/*")[0] == 0
    mine = EventLog(repo, "owner")
    mine.append("task.updated", "T3", {"line": "1"})
    mine.append(
        "lease.acquired",
        "T3",
        {"holder": "owner", "at": time.time(), "ttl_s": 1800, "globs": ["src/*"]},
    )
    _lined_other(repo, "src/new.py")
    monkeypatch.chdir(repo)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "new.py").write_text("u = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/new.py"], check=True)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1 and "T2 (other)" in msg, msg
