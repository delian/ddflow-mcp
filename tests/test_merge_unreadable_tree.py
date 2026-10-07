"""`merge` over a worktree git cannot read refuses as exactly that (Bb2f7566528).

`W.dirty` answers one `unreadable: ...` line for such a tree; `merge` passed it on as a
dirty FILE -- "1 uncommitted file(s) ... commit them, or pass --allow-dirty", wrong
advice, and a non-path in the JSON `dirty` list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow import api

OK, REFUSED = 0, 3


def _broken_tree(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _out, err = run_cli(repo, "claim", "T1", agent="w")
    assert code == OK, err
    code, out, _ = run_cli(repo, "--json", "show", "T1")
    tree = Path(json.loads(out)["worktree"])
    tree = tree if tree.is_absolute() else repo / tree
    (tree / ".git").write_text("gitdir: /nonexistent/broken\n")  # git cannot read it now
    pass_pipeline(repo, "T1")
    return tree


def test_an_unreadable_tree_is_refused_as_unreadable_not_as_dirty(repo):
    _broken_tree(repo)
    out = api.merge_item(repo, "T1", agent="w")
    assert out.exit == REFUSED, out
    assert "uncommitted file" not in out.reason, out.reason
    assert "could not read" in out.reason, out.reason
    assert not out.data.get("dirty") and out.data.get("dirty_unknown") is True, out.data


def test_allow_dirty_does_not_merge_a_tree_nobody_can_read(repo):
    _broken_tree(repo)
    out = api.merge_item(repo, "T1", agent="w", allow_dirty=True)
    assert out.exit == REFUSED and "could not read" in out.reason, out
