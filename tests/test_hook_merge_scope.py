"""A merge commit is judged on what the BRANCH changed, never on what the merged parent
brings in (DX-commit-hook-merge-untouched-files).

Merging main into an item's branch stages every path main changed. The lease check
already dropped those paths once a conflict was resolved by hand (test_hook_merge_from_base);
the generated-view and stale-doc checks did not, so a rule file or a renamed function that
arrived from main refused a merge the agent had no part in. These run through a real
`git commit` and the installed hook, since the hook is where it is enforced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_proc as _git

from ddflow.api import lifecycle as A

AGENT = "agent-merger"
STALE_RULE = (
    'id = "r-zz"\ntitle = "T"\nscope = "project"\npriority = 50\n'
    'created = "2026-10-16T02:11:16+00:00"\nupdated = "2026-10-16T02:11:16+00:00"\n\ndo it\n'
)


def _config(repo: Path, extra: str = "") -> None:
    cfg = repo / ".ddflow" / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    cfg.write_text(
        text.split("[enforce]")[0]
        + '[enforce]\ncommit_without_lease = "block"\ngenerated_views = "block"\n'
        f'stale_docs = "block"\n{extra}'
    )


def _commit_all(repo: Path, msg: str, *paths: str):
    """Commit ``paths`` (default: all) unchecked. Never `-A` after the claim: the event
    log is untracked then, and main would swallow it."""
    _git(repo, "add", "-A", "--", *(paths or ["."]))
    return _git(repo, "commit", "-qm", msg, "--no-verify")


@pytest.fixture
def merging(repo, monkeypatch):
    """`feat` holds a lease on `own.py` only; `base` is committed on main."""
    monkeypatch.setenv("DDFLOW_AGENT", AGENT)
    run_cli(repo, "adopt", "--agents", "claude")
    _config(repo)
    (repo / "own.py").write_text("x = 1\n")
    (repo / "shared.txt").write_text("base\n")
    (repo / "src").mkdir()
    (repo / "src" / "knobs.py").write_text("def renew_lease(holder):\n    return holder\n")
    _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "feat")
    run_cli(repo, "task", "add", "T1", "--globs", "own.py")
    assert A.claim(repo, "T1", no_worktree=True, agent=AGENT).ok
    return repo


def _main_moves(repo: Path, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "main")
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _commit_all(repo, "main moves on", *files)
    _git(repo, "checkout", "-q", "feat")


def _merge_no_commit(repo: Path, *, conflict: bool = False) -> None:
    r = _git(repo, "merge", "--no-commit", "--no-ff", "main")
    assert (r.returncode != 0) == conflict, r.stdout + r.stderr


def test_a_merge_bringing_files_outside_the_lease_passes(merging):
    _main_moves(merging, {"from_main.txt": "m\n", "docs/other.md": "m\n"})
    (merging / "own.py").write_text("x = 2\n")
    _git(merging, "add", "own.py")
    r = _git(merging, "commit", "-qm", "own")
    assert r.returncode == 0, r.stderr
    _merge_no_commit(merging)
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode == 0, r.stderr


def test_a_hand_resolved_merge_bringing_files_outside_the_lease_passes(merging):
    (merging / "own.py").write_text("x = feat\n")
    _git(merging, "add", "own.py")
    assert _git(merging, "commit", "-qm", "own").returncode == 0
    _main_moves(merging, {"own.py": "x = main\n", "from_main.txt": "m\n"})
    _merge_no_commit(merging, conflict=True)
    (merging / "own.py").write_text("x = both\n")
    _git(merging, "add", "own.py")
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode == 0, r.stderr


def test_a_branch_commit_editing_an_unleased_file_is_still_refused(merging):
    (merging / "shared.txt").write_text("feat\n")
    _git(merging, "add", "shared.txt")
    r = _git(merging, "commit", "-qm", "edit")
    assert r.returncode != 0 and "shared.txt" in r.stderr, r.stderr


def test_a_conflict_resolution_edit_to_an_unleased_file_is_still_refused(merging):
    (merging / "shared.txt").write_text("feat\n")
    _git(merging, "commit", "-qam", "feat edits shared", "--no-verify")
    _main_moves(merging, {"shared.txt": "main\n", "from_main.txt": "m\n"})
    _merge_no_commit(merging, conflict=True)
    (merging / "shared.txt").write_text("both\n")
    _git(merging, "add", "shared.txt")
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode != 0 and "shared.txt" in r.stderr, r.stderr
    assert "from_main.txt" not in r.stderr, r.stderr


def test_a_view_or_rule_file_from_main_is_not_judged_by_the_branch(merging):
    _main_moves(merging, {".ddflow/rules/r-zz-main.toml": STALE_RULE})
    _merge_no_commit(merging)
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode == 0, r.stderr


def test_a_rule_file_the_branch_commits_is_still_judged(merging):
    (merging / ".ddflow" / "rules").mkdir(parents=True, exist_ok=True)
    (merging / ".ddflow" / "rules" / "r-zz-mine.toml").write_text(STALE_RULE)
    _git(merging, "add", "-A")
    r = _git(merging, "commit", "-qm", "rule")
    assert r.returncode != 0 and "r-zz-mine.toml" in r.stderr, r.stderr


def test_a_rename_on_main_is_not_a_stale_doc_of_the_branch(merging):
    (merging / "own.py").write_text("x = 2\n")
    (merging / "docs").mkdir(exist_ok=True)
    (merging / "docs" / "guide.md").write_text("Call `renew_lease` first.\n")
    assert _commit_all(merging, "doc", "own.py", "docs").returncode == 0
    _main_moves(merging, {"src/knobs.py": "def extend_lease(holder):\n    return holder\n"})
    _merge_no_commit(merging)
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode == 0, r.stderr


def test_scope_all_restores_judging_everything_the_merge_stages(merging):
    _config(merging, 'merge_commit_scope = "all"\n')
    (merging / "own.py").write_text("x = feat\n")
    _git(merging, "commit", "-qam", "own", "--no-verify")
    _main_moves(merging, {"own.py": "x = main\n", "from_main.txt": "m\n"})
    _merge_no_commit(merging, conflict=True)
    (merging / "own.py").write_text("x = both\n")
    _git(merging, "add", "own.py")
    r = _git(merging, "commit", "-qm", "merge main")
    assert r.returncode != 0 and "from_main.txt" in r.stderr, r.stderr
