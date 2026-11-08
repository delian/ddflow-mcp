"""A git merge runs a custom merge driver (`pinsregen`) in a task worktree. Git writes the
driver's three inputs as `.merge_file_XXXXXX` temp files in the tree while it runs; a
concurrent `git add -A` stages them, then they vanish as git finishes, leaving an 'AD'
index entry that is not work and no lease covers (B-merge-driver-temp-staged). The lease
check must ignore these transient git temp files instead of refusing the commit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_hook_lapsed_lease import _setup

from ddflow.services import enforce as E


def test_a_staged_git_merge_temp_file_is_not_refused_outside_the_lease(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)  # owner holds src/*; src/new.py staged in that tree
    cfg.enforce.commit_without_lease = "block"
    (tree / ".merge_file_xYz123").write_text("temp\n")
    subprocess.run(["git", "-C", str(tree), "add", ".merge_file_xYz123"], check=True)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 0, msg
    assert ".merge_file_xYz123" not in msg
