"""The content of the files a review round covered (bug B-triage-guard-compares-whole-tree).

A pass recorded by triage must not cover work the reviewer did not see (B835d22b138), but
the WHOLE tree is too coarse a witness: a merge of main, an event-log commit or an edit to
a file the item never touched moves it. The round records `reviewed_paths` -- each path of
the item's changed set with its mode and blob id -- and the guard compares only those paths,
plus any path the item changes now. Reuses `evidence`'s entries; no second hashing scheme.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import evidence as GE

#: The evidence field a review round carries.
FIELD = "reviewed_paths"


def snapshot(wt: Path | str, paths: Iterable[str]) -> dict[str, str] | None:
    """{path: "mode oid"} of ``paths`` as ``wt`` holds them (a path it lacks is left out), or
    None when the tree cannot be read."""
    entries = GE.worktree_entries(wt)
    if entries is None:
        return None
    return {p: f"{entries[p][0]} {entries[p][1]}" for p in sorted(set(paths)) if p in entries}


def differing(
    recorded: dict[str, str], wt: Path | str, now_changed: Iterable[str]
) -> list[str] | None:
    """The paths whose content differs between the round's ``recorded`` snapshot and ``wt``
    now, over the recorded paths and ``now_changed``; None when ``wt`` cannot be read."""
    now = snapshot(wt, [*recorded, *now_changed])
    if now is None:
        return None
    return sorted(p for p in recorded.keys() | now.keys() if recorded.get(p) != now.get(p))
