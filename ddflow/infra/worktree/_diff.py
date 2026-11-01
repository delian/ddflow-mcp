"""The review diff: every changed path, new files included, nothing silently missing."""

from __future__ import annotations

import os
from pathlib import Path

from ...core import unidiff
from .. import git as G
from .. import worktree as _W


def capture_diff(
    tree: Path,
    base: str = "",
    *,
    include_untracked: bool = True,
    exclude: tuple[str, ...] = (),
    since: str = "",
) -> str:
    """The diff a reviewer should actually see, including NEW files.

    `git diff` omits untracked files entirely. A reviewer handed that diff cannot see
    the regression test you just wrote — and then reports "this change has no tests",
    which is both wrong and expensive, because it is exactly the finding a careful
    reviewer is supposed to produce. `git add -N` (intent-to-add) registers untracked
    paths in the index so they appear as new-file diffs, without staging their content.

    Ordering matters: intent-to-add FIRST, then one `git diff HEAD` that covers tracked
    modifications and new files together. Concatenating two separate diffs produces
    duplicate headers when a file is both modified and re-added.

    ``exclude`` drops paths starting with any of those prefixes from every part of it.
    Untracked paths are listed with `-z`, so a non-UTF-8 name is added as the file it
    is rather than as its C-quoted spelling, which names no file (B7ab10b58f2).

    ``since`` is the commit the committed part is taken from (default: ``base``'s merge
    base with HEAD). Raises RuntimeError when git cannot produce a part of the diff: a
    failure is never an empty "nothing changed".
    """
    untracked = untracked_files(tree, exclude)
    spec = ["--", ".", *(f":(exclude){p}" for p in exclude)] if exclude else []
    if include_untracked and untracked:
        _W.git(tree, "add", "-N", "--", *untracked)
    try:
        if since or base:
            if since:
                merge_base = since
            else:
                found = _W.git(tree, "merge-base", base, "HEAD")
                # Exit 1 is git's ANSWER "no common ancestor" (unrelated or shallow
                # history): the two commits are still diffed as they are. Anything else
                # is a git that could not say.
                if found.code != 1 and (not found.ok or not found.out):
                    raise RuntimeError(
                        f"git merge-base {base} HEAD in {tree} failed: {found.err or found.out}"
                    )
                merge_base = found.out or base
            committed = _diff_text(tree, f"{merge_base}..HEAD", *spec)
        else:
            committed = ""
        working = _diff_text(tree, "HEAD", *spec)
    finally:
        if include_untracked and untracked:
            # Undo intent-to-add so the caller's index is exactly as we found it. A
            # review that leaves files staged changes what the next commit contains.
            _W.git(tree, "reset", "--quiet", "--", *untracked)
    return "\n".join(part for part in (committed, working) if part.strip())


#: The prefixes `diff_covers_everything` matches: pinned, so a user's `diff.noprefix` or
#: `diff.mnemonicPrefix` cannot make every path look absent.
_DIFF_PREFIXES = ("--src-prefix=a/", "--dst-prefix=b/")


def _diff_text(tree: Path, *args: str) -> str:
    """`git diff <args>` as text a reviewer can read, whatever the bytes: a file's
    non-UTF-8 content (or a non-UTF-8 name under `core.quotepath=false`) is replaced, not
    raised (`git()` replaces by default; stated here because a strict decode once aborted the
    whole review)."""
    r = _W.git(tree, "diff", "--no-color", *_DIFF_PREFIXES, *args, errors="replace")
    if not r.ok:  # could not run: never "nothing changed"
        raise RuntimeError(f"git diff in {tree} failed: {r.err or r.out}")
    return r.out


def untracked_files(tree: Path, exclude: tuple[str, ...] = ()) -> list[str]:
    """Paths in ``tree`` git does not track and does not ignore, sorted -- read with `-z`,
    so a non-ASCII name is the file's own name (B7ab10b58f2)."""
    listed = G.files(tree, "untracked") or []
    return sorted(p for p in listed if p.strip() and not p.startswith(exclude))


def diff_covers_everything(
    tree: Path, diff: str, *, ignore_untracked: bool = False
) -> tuple[bool, list[str]]:
    """Cross-check: every path git reports as changed must appear in the diff.

    A silent omission is the failure this guards -- and it is silent by construction,
    because a diff that is missing a file looks exactly like a diff of a change that
    did not touch that file. The diff's files are read EXACTLY (`core.unidiff`), so `foo`
    is never found in the header of a changed `foo bar`; `capture_diff` pins the `a/` and
    `b/` prefixes, whatever `diff.noprefix` or `diff.mnemonicPrefix` say.
    """
    changed = _status_paths(tree, ignore_untracked)
    if changed is None:  # git could not say: never read as "nothing changed"
        return False, ["(git status failed: the diff cannot be checked)"]
    seen = [p for f in unidiff.files(diff) for p in f.paths]
    shown = set(seen)

    # The diff text is decoded with errors="replace" (`_diff_text`), so under
    # `core.quotepath=false` a non-UTF-8 byte in a name is U+FFFD there but an escape in
    # the status path. Such names cannot be told apart in the diff, so a lossy spelling
    # covers as many changed names as the diff holds sections for it, no more.
    def lossy(p: str) -> str:
        return os.fsencode(p).decode("utf-8", "replace")

    absent = [p for p in changed if p not in shown]
    room = {name: seen.count(name) for name in {lossy(p) for p in absent}}
    missing = []
    for p in absent:
        if room[lossy(p)] > 0:
            room[lossy(p)] -= 1
        else:
            missing.append(p)
    return (not missing), missing


def _status_paths(tree: Path, ignore_untracked: bool) -> list[str] | None:
    """Every path `git status` reports as changed: a rename as BOTH its paths rather than
    an `a -> b` line, and a non-ASCII name unquoted (B7ab10b58f2)."""
    entries = G.status(tree)
    if entries is None:
        return None
    out = [p for e in entries if not (ignore_untracked and e.untracked) for p in reversed(e.paths)]
    return [p for p in out if p]
