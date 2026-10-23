"""A review of a `--no-worktree` item that cannot find its work names every way out (B9a8c95a9da).

A research item whose deliverable is a committed doc, claimed without a worktree and reviewed
from where no branch of its own is checked out, got "Empty diff" and an UNAVAILABLE record; the
reporter found `--commit` / `--base` by trial. The message names them now, beside `--branch`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_no_worktree_review import NOTHING, _setup


def test_the_empty_diff_message_names_commit_and_base_beside_branch(repo, tmp_path):
    tree, _seen = _setup(repo, tmp_path)
    run_cli(tree, "claim", "T1", "--no-worktree")
    code, out, err = run_cli(repo, "review", "T1")
    assert code == NOTHING, out + err
    text = out + err
    assert "--branch" in text and "--commit" in text and "--base" in text, text
