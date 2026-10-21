"""What an item landed when it did not land through `ddflow merge` (bug B9eb92adafe).

`ddflow merge` records ``landed_before``/``landed_after``. Work that reached the base any
other way -- a plain `git merge`, a mixed code+doc commit, a swept commit -- left an item
with no landed range and no branch, so its ACTUAL change was unknown and the docs-only
pipeline could never apply. Two ways to know it, both VERIFIED, both failing closed (None:
the full task pipeline) and neither letting declared globs decide (B-na-required-gate):

- the commits on the base whose subject cites the item (``merge <id>: ...`` or ``<id>: ...``),
  each of which must touch at least one of the item's declared globs;
- a range the operator or agent recorded with ``ddflow update <id> --landed`` (`parse`),
  checked once, at record time, by `check`.
"""

from __future__ import annotations

from pathlib import Path

from ..core.globs import inside
from ..infra import worktree as W
from . import changes as CH


def _touches(item, paths: list[str] | None) -> bool:
    """Did ``paths`` reach something the item declared? An item declaring nothing has no
    claim to test a commit against, so nothing is evidence for it."""
    return bool(paths) and any(inside(p, g) for p in paths or () for g in item.globs)


def _cites(item_id: str, subject: str) -> bool:
    return subject.startswith((f"merge {item_id}:", f"{item_id}:"))


def cited_ranges(repo: Path, item, base: str) -> list[tuple[str, str]]:
    """``(before, after)`` for each commit on ``base`` that cites the item and touches its
    declared globs, oldest first. ``before`` is the first parent. Empty when git cannot say."""
    if not item.globs or not base:
        return []
    r = W.git(
        repo, "log", base, "--first-parent", "--reverse", "-F", f"--grep={item.id}:",
        "--format=%H%x00%P%x00%s",
    )  # fmt: skip
    out: list[tuple[str, str]] = []
    for line in r.out.splitlines() if r.ok else []:
        sha, _, rest = line.partition("\x00")
        parents, _, subject = rest.partition("\x00")
        first = parents.split()
        if not first or not _cites(item.id, subject):
            continue
        if _touches(
            item, CH.changed_paths(repo, first[0], tip=sha, include=("committed",), fork=False)
        ):
            out.append((first[0], sha))
    return out


def cited_paths(repo: Path, item, base: str) -> list[str] | None:
    """Every path the item's cited commits changed, or None when none is verifiable."""
    ranges = cited_ranges(repo, item, base)
    if not ranges:
        return None
    found: set[str] = set()
    for before, after in ranges:
        paths = CH.changed_paths(repo, before, tip=after, include=("committed",), fork=False)
        if paths is None:
            return None
        found.update(paths)
    return sorted(found)


def parse(repo: Path, spec: str) -> tuple[str, str] | str:
    """``(before, after)`` for ``--landed <sha>`` (the commit and its first parent) or
    ``<before>..<after>``; else why it is not one."""
    spec = spec.strip()
    if ".." in spec:
        a, _, b = spec.partition("..")
        before, after = W.rev(repo, a), W.rev(repo, b)
        if not before or not after:
            return f"--landed {spec!r}: {'the start' if not before else 'the end'} is not a commit here"
        return before, after
    after = W.rev(repo, spec)
    if not after:
        return f"--landed {spec!r} is not a commit here"
    before = W.rev(repo, f"{after}^1")
    if not before:
        return f"--landed {spec!r} has no parent to diff against: give <before>..<after>"
    return before, after


def check(repo: Path, item, base: str, before: str, after: str) -> str:
    """Why ``before..after`` may not be recorded as ``item``'s landing, or ``""``: both ends
    on ``base``, in order, and the change touching the item's declared globs."""
    for sha in (before, after):
        if not W.git(repo, "merge-base", "--is-ancestor", sha, base).ok:
            return f"{sha[:12]} is not an ancestor of {base}: only work that landed there counts"
    if not W.git(repo, "merge-base", "--is-ancestor", before, after).ok:
        return f"{before[:12]} is not an ancestor of {after[:12]}"
    paths = CH.changed_paths(repo, before, tip=after, include=("committed",), fork=False)
    if not paths:
        return "that range changes nothing (or git cannot say)"
    if not _touches(item, paths):
        return f"that range touches none of the item's declared globs ({', '.join(item.globs) or 'none'})"
    return ""
