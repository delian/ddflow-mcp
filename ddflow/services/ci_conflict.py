"""Why the CI gate's scratch merge conflicted, in words an agent can act on
(DX-ci-merge-conflict-cause): the files, whether they are pin files, and the remedy."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from ..infra import worktree as W


def conflicting_files(tree: Path) -> list[str]:
    """The paths left unmerged in ``tree`` after a conflicted `git merge`."""
    r = W.git(tree, "diff", "--name-only", "--diff-filter=U")
    return sorted(set(r.out.split())) if r.ok else []


def cause(tree: Path, base: str, pins: Callable[[], Iterable[str]] | None = None) -> str:
    """ "; conflicting files: ...; <remedy>", or "" when git names none. A conflict in a pin
    file is not hand-merged: it is regenerated (`ddflow pins regenerate`, which the
    post-merge hook runs under `[pins].after_merge`)."""
    files = conflicting_files(tree)
    if not files:
        return ""
    pinned = set(pins() if pins else ())
    mine = [f for f in files if f in pinned]
    out = f"; conflicting files: {', '.join(files)}"
    if mine:
        out += (
            f"; {', '.join(mine)} {'is a pin file' if len(mine) == 1 else 'are pin files'}: "
            f"merge {base} into the branch and run `ddflow pins regenerate` "
            f"(or set [pins].after_merge so the post-merge hook does), never hand-merge it"
        )
    rest = [f for f in files if f not in pinned]
    if rest:
        out += f"; resolve {', '.join(rest)} by merging {base} into the branch"
    return out
