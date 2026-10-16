"""When a leased worktree last showed work: a commit of its own, or a changed file.

The progress half of the breakable lease (`services.leasebreak`): a heartbeat proves a
process is alive, not that it is doing anything, so the question is asked of the tree.
Read-only; an unreadable tree is never read as idle (git could not tell: infinity), never an error.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from . import git as G


def last_commit_at(tree: Path, base: str, branch: str) -> float:
    """Committer time of the newest commit on ``branch`` that ``base`` does not have, or 0."""
    if not (base and branch):
        return 0.0
    r = G.run(tree, "log", "-1", "--format=%ct", f"{base}..{branch}")
    return float(r.out) if r.ok and r.out.strip().isdigit() else 0.0


def last_change_at(tree: Path) -> float:
    """Newest mtime among the tree's uncommitted files, 0 when it is clean, and infinity when git
    could not say (an unreadable tree counts as active, so it is never broken on a guess)."""
    entries = G.status(tree, untracked="all")
    if entries is None:  # git could not tell: never read that as "idle"
        return float("inf")
    newest = 0.0
    for e in entries:
        for rel in e.paths:
            try:
                newest = max(newest, os.stat(tree / rel).st_mtime)
            except OSError:  # a deleted file: its directory changed when it went
                with contextlib.suppress(OSError):
                    newest = max(newest, os.stat((tree / rel).parent).st_mtime)
    return newest


def last_activity(tree: Path, base: str, branch: str) -> float:
    """The newest of the tree's own commit and its newest uncommitted change."""
    if not tree.is_dir():
        return 0.0
    return max(last_commit_at(tree, base, branch), last_change_at(tree))
