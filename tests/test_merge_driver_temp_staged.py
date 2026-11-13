"""A git merge runs a custom merge driver (`pinsregen`) in a task worktree. Git writes the
driver's three inputs as `.merge_file_XXXXXX` temp files in the tree while it runs; a
concurrent `git add -A` stages them, then they vanish as git finishes, leaving an 'AD'
index entry that is not work and no lease covers (B-merge-driver-temp-staged). The lease
check must unstage these transient git temp files instead of committing the blob, while
still refusing a staged path that merely shares the `.merge_file_` prefix.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_hook_lapsed_lease import _setup

from ddflow.services import enforce as E


def _staged_names(tree: Path) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(tree), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout.split()


def test_a_staged_git_merge_temp_file_is_unstaged_instead_of_refused(repo, cfg, monkeypatch):
    # owner holds src/*; src/new.py staged in that tree. The driver writes
    # .merge_file_xYz123 (git mkstemp turns XXXXXX into six chars), `git add -A` stages
    # it, then git finishes and deletes it -- the bug's own 'AD' residue.
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    (tree / ".merge_file_xYz123").write_text("temp\n")
    subprocess.run(["git", "-C", str(tree), "add", ".merge_file_xYz123"], check=True)
    (tree / ".merge_file_xYz123").unlink()
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 0, msg
    assert ".merge_file_xYz123" not in msg
    # The filter alone must not leave the 'AD' entry staged: the commit would write the
    # transient blob into history. It is unstaged, so only nothing the agent made stays.
    assert ".merge_file_xYz123" not in _staged_names(tree)
    # ... and the unstage is surgical: the owner's own staged work survives the reset.
    assert "src/new.py" in _staged_names(tree)


def test_a_merge_file_prefixed_non_temp_path_is_still_refused(repo, cfg, monkeypatch):
    # `.merge_file_payload` is not git's mkstemp temp name (no six-char tail), so the
    # narrowed predicate must still refuse it outside the owner's src/* lease.
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    (tree / ".merge_file_payload").write_text("payload\n")
    subprocess.run(["git", "-C", str(tree), "add", ".merge_file_payload"], check=True)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1
    assert ".merge_file_payload" in msg
