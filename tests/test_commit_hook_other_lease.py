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
