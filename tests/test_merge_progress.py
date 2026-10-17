"""`ddflow merge` says what it is doing once the commit has landed
(merge-silent-during-post-merge-ci).

The post-merge check and the event-log commit run for minutes after the merge commit is on
the base; the command printed nothing until it finished, and an operator's tool timeout
killed what looked like a hang.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK = 0


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def test_merge_prints_progress_on_stderr_once_it_has_landed(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == OK, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "c.txt").write_text("branch\n")
    _git(tree, "commit", "-qam", "branch edit")
    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == OK, out + err
    assert "merged T1 into main" in err, err
    assert "post-merge check" in err and "[ci].on_merge" in err, err
