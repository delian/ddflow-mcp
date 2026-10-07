"""Bug B54909478c4: `worktree.removed` had three writers with two payload shapes: cleanup
and the PR flow wrote the item's recorded worktree (`it.worktree`, as `claim` stored it),
`merge` wrote the resolved absolute path. One writer now, one shape."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def test_merge_records_the_worktree_as_claim_recorded_it(repo) -> None:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    recorded = fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "add", "a.py")
    _git(wt, "commit", "-qm", "a")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == 0, out + err
    removed = [e.data for e in EventLog(repo).read_all() if e.kind == "worktree.removed"]
    assert removed == [{"path": recorded}]
