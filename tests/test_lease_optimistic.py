"""Optimistic concurrency: disjoint hunks of one file may be edited by two agents (DX-lease-hunk-optimistic).

A per-file lease mostly guards a merge conflict git can already auto-resolve: two branches
editing DISJOINT hunks of one file merge cleanly, so the lease only needs to order the
merges, not serialise the edits. When `[locks].optimistic` is on, a plain claim whose globs
overlap a live holder's on a non-generated file is GRANTED when a `git merge-tree` probe shows
the holder's branch merges cleanly into the base, and the overlapping glob is recorded
`optimistic` on the lease; it is still refused when the merge would really conflict, when the
holder's branch cannot be found, or when the knob is off (D-lease-overlap-only-by-break).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _tasks(repo: Path) -> None:
    """A phase with two tasks, both declaring `shared.txt`."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "A", {"parent": "P1", "title": "a", "globs": ["shared.txt"]})
    log.append("task.added", "B", {"parent": "P1", "title": "b", "globs": ["shared.txt"]})


def _optimistic_on(repo: Path) -> None:
    (repo / ".ddflow").mkdir(exist_ok=True)
    with (repo / ".ddflow" / "config.toml").open("a") as f:
        f.write("\n[locks]\noptimistic = true\n")


def _branch_edit(repo: Path, name: str, path: str, old: str, new: str) -> None:
    """Create `repo`'s branch ``name`` at main, rewrite ``old`` to ``new`` in ``path``.

    Leaves the working tree back on main, nothing staged."""
    _git(repo, "checkout", "-q", "-b", name, "main")
    (repo / path).write_text((repo / path).read_text().replace(old, new, 1))
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", f"{name}: {path}")
    _git(repo, "checkout", "-q", "main")


def test_a_clean_merge_grants_the_overlapping_claim(repo: Path) -> None:
    (repo / "shared.txt").write_text("one\ntwo\nthree\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "shared")
    _tasks(repo)
    _optimistic_on(repo)
    _branch_edit(repo, "ddflow/A", "shared.txt", "three\n", "three\nfour\n")  # appends a line
    assert LC.claim(repo, "A", agent="a", no_worktree=True).exit == 0
    out = LC.claim(repo, "B", agent="b", no_worktree=True)
    assert out.exit == 0, out.reason
    st = fold(EventLog(repo, "r").read_all())
    assert st.items["B"].lease is not None
    assert st.items["B"].lease.optimistic == ["shared.txt"], st.items["B"].lease


def test_a_real_conflict_stays_exclusive(repo: Path) -> None:
    (repo / "shared.txt").write_text("one\ntwo\nthree\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "shared")
    _tasks(repo)
    _optimistic_on(repo)
    _branch_edit(repo, "ddflow/A", "shared.txt", "three\n", "THREE\n")  # A edits line 3
    (repo / "shared.txt").write_text("one\ntwo\nXXX\n")  # main edits the same line
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "main: line 3")
    assert LC.claim(repo, "A", agent="a", no_worktree=True).exit == 0
    out = LC.claim(repo, "B", agent="b", no_worktree=True)
    assert out.exit == 3, "granted over a real conflict"
    assert "shared.txt" in out.reason and "A" in out.reason, out.reason


def test_the_knob_off_keeps_the_plain_refusal(repo: Path) -> None:
    (repo / "shared.txt").write_text("one\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "shared")
    _tasks(repo)
    _branch_edit(repo, "ddflow/A", "shared.txt", "one\n", "one\ntwo\n")
    assert LC.claim(repo, "A", agent="a", no_worktree=True).exit == 0
    out = LC.claim(repo, "B", agent="b", no_worktree=True)
    assert out.exit == 3, out.reason


def test_an_unknown_holder_branch_fails_closed(repo: Path) -> None:
    (repo / "shared.txt").write_text("one\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-qm", "shared")
    _tasks(repo)
    _optimistic_on(repo)
    # A holds shared.txt but has no `ddflow/A` branch: the probe cannot answer, so B stays refused.
    assert LC.claim(repo, "A", agent="a", no_worktree=True).exit == 0
    out = LC.claim(repo, "B", agent="b", no_worktree=True)
    assert out.exit == 3, out.reason
