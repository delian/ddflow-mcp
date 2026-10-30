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

from ...core import globs
from .evidence import worktree_entries

#: The evidence field a review round carries.
REVIEWED_PATHS_FIELD = "reviewed_paths"


def path_snapshot(wt: Path | str, paths: Iterable[str]) -> dict[str, str] | None:
    """{path: "mode oid"} of ``paths`` as ``wt`` holds them, or None when the tree cannot be
    read. A path ``wt`` lacks (deleted) is "": absent is a state to compare, not a key to
    drop, or a file deleted after the round would match a snapshot that never named it."""
    entries = worktree_entries(wt)
    if entries is None:
        return None
    return {
        p: f"{entries[p][0]} {entries[p][1]}" if p in entries else "" for p in sorted(set(paths))
    }


def snapshot_differing(
    recorded: dict[str, str], wt: Path | str, now_changed: Iterable[str]
) -> list[str] | None:
    """The paths whose content differs between the round's ``recorded`` snapshot and ``wt``
    now, over the recorded paths and ``now_changed``; None when ``wt`` cannot be read."""
    now = path_snapshot(wt, [*recorded, *now_changed])
    if now is None:
        return None
    return sorted(p for p in recorded.keys() | now.keys() if recorded.get(p) != now.get(p))


def ignorable(
    path: str,
    recorded: dict[str, str] | None,
    ignore: Iterable[str],
    docs: Iterable[str] = (),
    gone: Iterable[str] = (),
) -> bool:
    """Is ``path``'s change since the round not new work?

    Two classes, and the difference is that documentation is word, not code:

    * a path the round reviewed that matches ``docs`` (`[review].doc_globs`) and still
      exists is never new work (bug Bb89ff23f21): a one-line doc edit after a triage
      invalidated refuted gates and a wording-only edit after the final pass still warned
      "different tree" at complete, because the reviewed doc moved. A review judges the
      CODE; clarified wording is not a defect to re-review. A reviewed doc that was
      DELETED (``gone``) is a change like any other, not wording: removing reviewed
      content is new work. A doc the round did not review is not exempt by name alone.
    * any other path is one the round did not review (it is not in its ``recorded``
      snapshot) that matches ``ignore`` (`[review].triage_ignore_globs`: tests answering a
      finding). A reviewed code path is never ignored: its content was judged, whatever it
      is called.

    The one predicate for triage, `sync` and `complete`, so the three cannot drift."""
    if path in (recorded or {}) and path not in gone and any(globs.match(path, g) for g in docs):
        return True
    return path not in (recorded or {}) and any(globs.match(path, g) for g in ignore)
