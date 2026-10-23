"""What differs, in words, for a passed gate whose evidence names another tree."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .evidence import TreeEntries, commit_tree_entries, differing_paths


def _what_differs(
    cwd: Path,
    base: str,
    dirt: str,
    now: TreeEntries | None,
    label: str,
    own: Sequence[str] | None = None,
) -> str:
    """Name the files, when the tree the gate ran on can be rebuilt (a clean commit). "" when
    ``own`` is given, the tree can be rebuilt, and none of the item's own paths differ."""
    then = commit_tree_entries(cwd, base) if base else None
    paths = differing_paths(then, now) if then is not None and now is not None else []
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
    differ (``make`` is `outcomes.StaleNote`)."""
    why = _what_differs(cwd, base, dirt, now, label, own)
    return [make(gid, why)] if why else []
