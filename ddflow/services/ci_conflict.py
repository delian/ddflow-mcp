"""Why the CI gate's scratch merge conflicted, in words an agent can act on
(DX-ci-merge-conflict-cause): the files, whether they are pin files, and the remedy."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from ..infra import worktree as W


def conflicting_files(tree: Path) -> list[str]:
    """The paths left unmerged in ``tree`` after a conflicted `git merge`."""
    r = W.git(tree, "diff", "--name-only", "-z", "--diff-filter=U")
    return sorted({f for f in r.out.split("\0") if f}) if r.ok else []


def cause(tree: Path, base: str, pins: Callable[[], Iterable[str]] | None = None) -> str:
    """ "; conflicting files: ...; <remedy>", or "" when git names none. A conflict in a pin
    file is not hand-merged: after merging the base into the branch, `ddflow pins regenerate`
    settles the conflicted pin and re-records it."""
    files = conflicting_files(tree)
    if not files:
        return ""
    pinned = set(pins() if pins else ())
    mine = [f for f in files if f in pinned]
    out = f"; conflicting files: {', '.join(files)}"
    if mine:
        out += (
            f"; {', '.join(mine)} {'is a pin file' if len(mine) == 1 else 'are pin files'}: "
            f"merge {base} into the branch, then run `ddflow pins regenerate` "
            f"to settle and re-record it; never hand-merge it"
        )
    rest = [f for f in files if f not in pinned]
    if rest:
        out += f"; resolve {', '.join(rest)} by merging {base} into the branch"
    return out
