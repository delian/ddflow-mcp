"""`reviewed_paths` is the content the reviewer was SENT, not the tree when the round ends
(bug B44548e189d).

A review runs for minutes. The snapshot was taken when the round was RECORDED, so a file
changed while the reviewer ran was claimed as reviewed, and the next round was refused
'nothing new to review' for a change no reviewer saw.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_review_triage import _setup, _state
from test_review_triage_scoped_tree import _git

import ddflow.api.review as api


def test_a_file_changed_while_the_reviewer_runs_is_not_in_the_snapshot(repo, tmp_path):
    _setup(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("enabled = false", "enabled = true"))
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).unlink()
    _git(repo, "add", "-A")
    run_cli(repo, "claim", "T1")
    wt = (repo / _state(repo).worktree).resolve()
    (wt / "a.py").write_text("x = 1  # FINDA " + "p" * 60 + "\n")
    _git(wt, "add", "-A")
    # the reviewer, while it runs, changes the item's tree: a commit made mid-review
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        cli.read_text().replace(
            "in=$(cat)\n",
            f"in=$(cat)\necho 'y = 2' > '{wt}/late.py'; git -C '{wt}' add late.py\n",
            1,
        )
    )
    out = api.review(repo, gate="critic", item="T1", full=True)
    assert out.data["outcome"] == "failed", out.reason
    snap = _state(repo).gates["critic"].evidence["reviewed_paths"]
    assert "a.py" in snap
    assert "late.py" not in snap, "the reviewer never saw late.py"
