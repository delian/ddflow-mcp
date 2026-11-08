"""What differs, in words, for a passed gate whose evidence names another tree."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ...config import Config
from ...infra import git as GIT
from ..review_exclude import _hit, patterns
from .evidence import TreeEntries, commit_tree_entries, differing_paths


def _gate_covered(cwd: Path, paths: list[str], then: TreeEntries | None) -> list[str]:
    """The subset of ``paths`` the gate ran on: a tracked file the review did not leave out.

    A path the gate covered is one the reviewed trees track: a git MERGE temporary is tracked
    nowhere so it is not a difference, but a file DELETED from the index is still tracked in the
    reviewed base tree and a file ADDED is tracked in the index -- either is covered, so both
    keep (the base-only set drops an addition, the index-only set drops a deletion). A file
    matched by `[review].exclude_globs` (a golden snapshot/transcript) or owned by `pins.toml`
    was never sent to the reviewer, so naming it is not cleared by reviewing the covered code
    (B-stale-mark-names-merge-temp-and-excluded-files).
    """
    if not paths:
        return []
    tracked = GIT.files(cwd, "tracked")
    paths = [
        p for p in paths
        if then is None or p in then or tracked is None or p in tracked
    ]
    if paths:
        excluded, pinned = patterns(Config.load(cwd), cwd)
        paths = [p for p in paths if not _hit(p, excluded, pinned)]
    return paths


def _what_differs(
    cwd: Path,
    base: str,
    dirt: str,
    now: TreeEntries | None,
    label: str,
    own: Sequence[str] | None = None,
) -> str:
    """Name the files, when the tree the gate ran on can be rebuilt (a clean commit). "" when
    ``own`` is given, the tree can be rebuilt, and none of the item's own paths differ.

    A file the note names must be content the gate ran on: an untracked file (a git merge
    temporary, tracked nowhere) is not, and neither is a tracked file the review left out --
    neither counts as a difference, so it is never named (B-stale-mark-names-merge-temp-and-
    excluded-files).
    """
    then = commit_tree_entries(cwd, base) if base else None
    paths = differing_paths(then, now) if then is not None and now is not None else []
    paths = _gate_covered(cwd, paths, then)
    if own is not None and paths:
        paths = [p for p in paths if p in set(own)]
        if not paths:
            return ""
    shown = ", ".join(paths[:8]) + (f" (+{len(paths) - 8} more)" if len(paths) > 8 else "")  # noqa: PLR2004
    if dirt == "clean" and paths:
        return f"it ran on {base}; {label} differs in {shown}"
    if paths:
        return (
            f"it ran on uncommitted edits over {base}, and {label} holds other content "
            f"(files changed since {base}: {shown})"
        )
    return f"the content it ran on is not the content of {label}"


def _differing_note(make, gid, cwd, base, dirt, now, label, own) -> list:
    """``[make(gid, why)]`` for a gate whose tree differs, [] when only paths outside ``own``
    differ (``make`` is `StaleNote`)."""
    why = _what_differs(cwd, base, dirt, now, label, own)
    return [make(gid, why)] if why else []


@dataclass(frozen=True, order=True)
class StaleNote:
    """A passed gate whose evidence is not about the tree being completed -- or, with
    ``unverified``, one whose evidence could not be compared with it at all. The two
    are kept apart: "could not tell" rendered as "fresh" is a silent pass, and rendered
    as "stale" is a false alarm."""

    gate: str
    why: str
    unverified: bool = False
