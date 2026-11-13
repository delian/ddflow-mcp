"""What an item changed on disk — the one reader every completion check uses.

Extracted from `rules.py` so `observed_globs` can import it without a deferred import:
`rules` imports `observed_globs` at module level, and this module imports neither, so the
two callers share one leaf instead of one late import.
"""

from __future__ import annotations

from pathlib import Path

from ...core.globs import inside
from ...infra import worktree as W
from .. import changes as CH
from .. import landed as LD
from .. import testselect as TS


def changed_paths(repo: Path, it, *, cache: dict | None = None) -> list[str] | None:
    """The paths the item changed: what landed (`landed_before..landed_after`), or before
    it lands its worktree (or, with none, its branch) against its base. None when git cannot say -- never an empty \"nothing changed\". A landing restricted to the item's own
    paths (`update --landed-paths`, B738d7bbeab) reports only the range's changed files
    inside those, never the whole commit's; a restriction the range no longer answers
    to is None (fail closed), never empty and never the whole commit.

    ``cache`` is a per-scan memo keyed by item id (Bc52bf2ef41): a scan over many merged
    items calls this once per verdict probe, so the diff and the default branch are asked
    of git once per item, not once per probe."""
    key = ("changed", it.id) if cache is not None else None
    if key is not None and key in cache:
        return cache[key]
    paths = _read(repo, it, cache)
    if key is not None:
        cache[key] = paths
    return paths


def _read(repo: Path, it, cache: dict | None) -> list[str] | None:
    if it.landed_before and it.landed_after:
        paths = CH.changed_paths(
            repo, it.landed_before, tip=it.landed_after, include=("committed",), fork=False
        )
        if it.landed_paths and paths is not None:
            # Never the whole commit's; when the range stopped holding the item's work,
            # None -- like every other "cannot say" here: full pipeline, never a guess
            # (verified at record time, so this needs the recorded range tampered with).
            matched = sorted(p for p in paths if any(inside(p, g) for g in it.landed_paths))
            return matched if matched else None
        return paths
    base = it.base or _default_branch(repo, cache)
    found, may_cite = _unlanded_paths(repo, it, base)
    if found or not may_cite:
        return found
    # Landed by a plain git merge or a mixed commit, so the branch (if any) differs from the
    # base by nothing: the commits that cite the item, checked.
    return LD.cited_paths(repo, it, base) or found


def _default_branch(repo: Path, cache: dict | None) -> str:
    """The repository's default branch, remembered once per scan in ``cache``."""
    key = ("base",) if cache is not None else None
    if key is not None and key in cache:
        return cache[key]
    base = W.default_branch(repo)
    if key is not None:
        cache[key] = base
    return base


def _unlanded_paths(repo: Path, it, base: str) -> tuple[list[str] | None, bool]:
    """The item's worktree (or, with none, its branch) against its base, and whether
    the item's cited commits may stand in for it. They may when nothing is left to read: a
    worktree that differs from the base by nothing, a branch already merged into its base, or
    no worktree and no branch at all. They may not when the item claims a worktree or branch
    that git cannot read (None): its real work is unseen, so never guess."""
    path = W.load_path(repo, it.worktree) if it.worktree else None
    if path and path.is_dir():
        seen = TS.changed_files(path, base)
        return seen, seen == []
    # Claimed --no-worktree: the work is a branch in a tree that is not ours to read.
    if it.branch and W.rev(repo, it.branch):
        out = CH.changed_paths(repo, base, tip=it.branch, include=("committed",))
        # Empty is not "nothing changed" here: a branch already merged into its base, or
        # the base itself, diffs to nothing whatever the task did.
        return (sorted(out) if out else None), True
    return None, not (it.worktree or it.branch)
