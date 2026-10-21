"""What an item landed when it did not land through `ddflow merge` (bug B9eb92adafe).

`ddflow merge` records ``landed_before``/``landed_after``. Work that reached the base any
other way -- a plain `git merge`, a mixed code+doc commit, a swept commit -- left an item
with no landed range and no branch, so its ACTUAL change was unknown and the docs-only
pipeline could never apply. Two ways to know it, both VERIFIED, both failing closed (None:
the full task pipeline) and neither letting declared globs decide (B-na-required-gate):

- the commits on the base whose subject cites the item (``merge <id>: ...`` or ``<id>: ...``),
  at least one of which must touch the item's declared globs, and ALL of which count toward
  the change (a cited commit that touched code is never left out);
- a range the operator or agent recorded with ``ddflow update <id> --landed`` (`parse`),
  checked once, at record time, by `check`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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


#: git's empty tree in a SHA-1 repository: the "before" of a root commit, which has no parent
#: to diff against. `empty_tree` asks the repository, whose object format may differ.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def empty_tree(repo: Path) -> str:
    r = W.git(repo, "hash-object", "-t", "tree", "/dev/null")
    return r.out if r.ok and r.out else _EMPTY_TREE


def _since(created_at: str) -> str:
    """The item's creation time less a margin: a commit date has whole seconds and the item's
    has fractions, and a clock a little behind must not hide the item's own commit."""
    try:
        t = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return "1970-01-01"
    return (t - timedelta(minutes=2)).isoformat()


def cited_ranges(repo: Path, item, base: str) -> list[tuple[str, str]]:
    """``(before, after)`` for EVERY commit on ``base`` that cites the item, oldest first,
    ``before`` being its first parent (the empty tree for a root commit). Every commit counts,
    a side branch's too: a plain `git merge` lands the cited commits under a merge commit that
    does not cite the item. A commit that cites the item but touches nothing it declared is
    still listed, so the code half of a split landing is never left out of the verdict.
    Only commits made since the item was created count: an id reused after removal
    (`--readd`) is not credited with the old item's work. Empty when git cannot say."""
    if not base:
        return []
    since = [f"--since={_since(item.created_at)}"] if item.created_at else []
    r = W.git(
        repo, "log", base, "--reverse", *since, "-F", f"--grep={item.id}:",
        "--format=%H%x00%P%x00%s",
    )  # fmt: skip
    out: list[tuple[str, str]] = []
    for line in r.out.splitlines() if r.ok else []:
        sha, _, rest = line.partition("\x00")
        parents, _, subject = rest.partition("\x00")
        if _cites(item.id, subject):
            out.append(((parents.split() or [empty_tree(repo)])[0], sha))
    return out


def cited_paths(repo: Path, item, base: str) -> list[str] | None:
    """Every path the item's cited commits changed, or None unless that is verifiable: at
    least one cited commit must touch what the item declared (the citation alone proves
    nothing), and git must be able to say what each one changed."""
    ranges = cited_ranges(repo, item, base)
    found: set[str] = set()
    touched = False
    for before, after in ranges:
        paths = CH.changed_paths(repo, before, tip=after, include=("committed",), fork=False)
        if paths is None:
            return None
        touched = touched or _touches(item, paths)
        found.update(paths)
    return sorted(found) if touched else None


def parse(repo: Path, spec: str) -> tuple[str, str] | str:
    """``(before, after)`` for ``--landed <sha>`` (the commit and its first parent) or
    ``<before>..<after>`` (a root commit's "before" is the empty tree); else why it is not one."""
    spec = spec.strip()
    if ".." in spec:
        a, _, b = spec.partition("..")
        before = a if a == empty_tree(repo) else W.rev(repo, a)
        after = W.rev(repo, b)
        if not before or not after:
            return f"--landed {spec!r}: {'the start' if not before else 'the end'} is not a commit here"
        return before, after
    after = W.rev(repo, spec)
    if not after:
        return f"--landed {spec!r} is not a commit here"
    return W.rev(repo, f"{after}^1") or empty_tree(repo), after


def check(repo: Path, item, base: str, before: str, after: str) -> str:
    """Why ``before..after`` may not be recorded as ``item``'s landing, or ``""``: both ends
    on ``base``, in order, and the change touching the item's declared globs. A root commit's
    range starts at the empty tree, and only a root commit's."""
    root = before == empty_tree(repo)
    if root and W.rev(repo, f"{after}^1"):
        return (
            f"{after[:12]} is not a root commit: the empty tree starts only a root commit's range"
        )
    for sha in (after,) if root else (before, after):
        if not W.git(repo, "merge-base", "--is-ancestor", sha, base).ok:
            return f"{sha[:12]} is not an ancestor of {base}: only work that landed there counts"
    if not root and not W.git(repo, "merge-base", "--is-ancestor", before, after).ok:
        return f"{before[:12]} is not an ancestor of {after[:12]}"
    paths = CH.changed_paths(repo, before, tip=after, include=("committed",), fork=False)
    if not paths:
        return "that range changes nothing (or git cannot say)"
    if not _touches(item, paths):
        return f"that range touches none of the item's declared globs ({', '.join(item.globs) or 'none'})"
    return ""
