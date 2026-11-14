"""A git merge runs a custom merge driver (`pinsregen`) in a task worktree. Git writes the
driver's three inputs as `.merge_file_XXXXXX` temp files in the tree while it runs; a
concurrent `git add -A` stages them, then they vanish as git finishes, leaving an 'AD'
index entry that is not work and no lease covers (B-merge-driver-temp-staged). The lease
check must unstage these transient git temp files instead of committing the blob, while
still refusing a staged path that merely shares the `.merge_file_` prefix.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_hook_lapsed_lease import _setup

from ddflow.services import enforce as E
from ddflow.services.enforce import leasecheck as LC


def _staged_names(tree: Path) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(tree), "diff", "--cached", "--name-only", "-z"],
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(x) for x in r.stdout.split(b"\x00") if x]


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
    # narrowed predicate must still refuse it outside the owner's src/* lease, and the
    # unstage must NOT drop it (only git's own temp files are unstage targets).
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    (tree / ".merge_file_payload").write_text("payload\n")
    subprocess.run(["git", "-C", str(tree), "add", ".merge_file_payload"], check=True)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1
    assert ".merge_file_payload" in msg
    assert ".merge_file_payload" in _staged_names(tree)


def test_a_unicode_merge_file_name_is_not_gits_ascii_temp(repo, cfg, monkeypatch):
    # git's mkstemp alphabet is ASCII only, so a SIX-codepoint non-ASCII tail is a real
    # name and still refused outside the owner's src/* lease, not mistaken for git's temp
    # (with only isalnum(), git would misclassify it and silently unstage it).
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    name = (
        ".merge_file_\u03a9\u00e9\u00e9\u00e9\u00e9\u00e9"  # six codepoints: the narrow tail length
    )
    (tree / name).write_text("payload\n")
    subprocess.run(["git", "-C", str(tree), "add", name], check=True)
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1
    assert name in msg
    assert name in _staged_names(tree)


def test_a_temp_file_whose_reset_fails_is_reported_not_silently_waived(repo, cfg, monkeypatch):
    # The unstage is a git call that can fail (git gone, killed at its timeout). Waiving
    # the path anyway would let the commit write the transient blob into history -- the
    # exact harm the fix exists to stop. `_unstage_git_temp` reports the path it could
    # not drop, so `_check_lease` refuses on it instead (fail closed).
    from ddflow.adapters import git as G

    tree = _setup(repo, cfg)
    (tree / ".merge_file_xYz123").write_text("temp\n")
    subprocess.run(["git", "-C", str(tree), "add", ".merge_file_xYz123"], check=True)
    (tree / ".merge_file_xYz123").unlink()
    monkeypatch.chdir(tree)
    staged = LC.staged_paths(repo)  # read first: the patch below reprograms only the reset
    monkeypatch.setattr(
        LC.G, "run", lambda *a, **k: G.GitResult(code=1, out="", err="", unavailable=True)
    )
    assert ".merge_file_xYz123" in LC._unstage_git_temp(repo, staged)
