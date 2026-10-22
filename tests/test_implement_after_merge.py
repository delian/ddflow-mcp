"""`implement` can be recorded after the item merged (B998bcca9ce).

`gate record implement --outcome passed` refused "no commit ahead of main" once the branch had
landed, and a skipped implement does not settle a required gate: after a merge that kept the
tree, the only way out was `--force`. A branch whose commits are already on the base is the
OPPOSITE of an empty branch -- the work landed -- so the check judges only an unlanded item.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog


def _git(*a: str) -> None:
    subprocess.run(["git", *a], check=True, capture_output=True)


def _item(repo: Path, commit: bool) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "T1", "globs": ["src/T1/*"]})
    out = LC.claim(repo, "T1", agent="worker")
    wt = Path(out.data["worktree"])
    if commit:
        (wt / "src" / "T1").mkdir(parents=True)
        (wt / "src/T1/a.py").write_text("x = 1\n")
        _git("-C", str(wt), "add", "-A")
        _git("-C", str(wt), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "w")
    return wt


def _record_implement(repo: Path):
    return run_cli(
        repo, "gate", "record", "T1", "implement", "--outcome", "passed", "--evidence", "done"
    )


def test_implement_is_recordable_after_the_merge_kept_the_tree(repo):
    _item(repo, commit=True)
    assert LC.merge(repo, "T1", agent="worker", keep=True).exit == 0
    code, out, err = _record_implement(repo)
    assert code == 0, out + err


def test_an_empty_unmerged_branch_is_still_refused(repo):
    _item(repo, commit=False)
    code, out, err = _record_implement(repo)
    assert code != 0 and "no commit ahead" in out + err
