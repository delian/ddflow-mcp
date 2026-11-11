"""Merging the base into a tree, and measuring what a tree holds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ...config import Config
from .. import git as G
from .. import worktree as _W
from ._locate import Worktree
from ._mergecore import _merge_retrying_stash


def sync(wt: Worktree, cfg: Config) -> G.GitResult:
    """Merge the base branch INTO the task branch, from inside the worktree.

    This direction is always safe: it never touches the primary checkout and never
    performs a checkout. The reverse (checking out base to update it) is what kills
    live sessions.
    """
    return _merge_retrying_stash(wt.path, ["merge", "--no-edit", wt.base], cfg)


def behind(wt: Worktree) -> int:
    r = _W.git(wt.path, "rev-list", "--count", f"HEAD..{wt.base}")
    return int(r.out) if r.ok and r.out.isdigit() else -1


def ahead(wt: Worktree) -> int:
    r = _W.git(wt.path, "rev-list", "--count", f"{wt.base}..HEAD")
    return int(r.out) if r.ok and r.out.isdigit() else -1


def dirty(wt: Worktree, *, untracked: bool = True) -> list[str]:
    """Uncommitted changes in a tree.

    ``untracked=False`` reports only MODIFIED TRACKED files, which is the right
    question to ask before a merge: git refuses a merge over local modifications, but
    untracked files are none of its business except in the one case it detects and
    reports itself. Counting untracked files as "dirty" made `merge` refuse forever in
    any repo holding a build directory, a virtualenv, or — as found here — ddflow's
    own freshly-created `.ddflow/` before it was committed.

    An unreadable tree (a broken ``.git`` file, no permission, no git) is never clean:
    it answers one line starting with `UNREADABLE`, so every caller that asks "anything
    uncommitted?" keeps the tree, and `unreadable` tells the two apart (B028b11b4cb).
    """
    r = G.status_run(wt.path, untracked="normal" if untracked else "no")
    entries = G.parse_status(r)
    if entries is None:
        why = (r.err or r.out).strip().splitlines() or [f"exit {r.code}"]
        return [f"{UNREADABLE}git status failed: {why[0]}"]
    return [e.line() for e in entries]


#: The prefix of the one line `dirty` answers for a tree git could not read. No porcelain
#: line can start with it (status columns are " MTADRCU?!"), so a caller that filters by
#: status code -- untracked ``??`` or ignored ``!!`` -- cannot drop it as one.
UNREADABLE = "unreadable: "


def unreadable(lines: list[str]) -> bool:
    """True when `dirty` could not read the tree: unknown, which is never clean."""
    return any(ln.startswith(UNREADABLE) for ln in lines)


#: Directory names that are disposable caches. A worktree holds work when its status shows
#: anything beyond caches: nearly every tree has a `.venv` or a `__pycache__`, and anything
#: untracked or ignored that is NOT one of these is work.
CACHES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        ".cache",
    }
)


def is_cache(name: str) -> bool:
    """Is ``name`` (a repo-relative path) inside a disposable cache directory?"""
    return any(part in CACHES for part in PurePosixPath(name).parts)


@dataclass(frozen=True)
class TreeWork:
    """What one worktree holds, measured once (B-uni-tree-lifecycle).

    Lease recovery, the cleanup survey and the onboarding sweep each asked git their own
    version of this and answered "does it hold work?" slightly differently; this is the
    one measurement they read, and each reads the view it always did:

    * ``dirty``: the porcelain lines of every uncommitted change git counts (ignored files
      excluded, caches INCLUDED) -- what `dirty` answers for a tree it could read; EMPTY when
      ``readable`` is False, which is not "clean": read ``readable`` first;
    * ``work``: the paths of those changes beyond caches; ``ignored``: the ignored paths
      beyond caches -- `git worktree remove` deletes ignored files without complaint, so a
      tree holding a `.env` is not clean;
    * ``ahead`` / ``behind``: commits against the base; -1 when git could not count them or
      no base was given. Unknown is never zero.

    ``readable`` is False when git could not run `status` (a broken `.git` file, no
    permission, no git): unknown is never clean. ``err`` is git's stderr and ``msg`` the
    first line of its answer, for the message that says why."""

    readable: bool
    err: str = ""
    msg: str = ""
    entries: tuple[G.StatusEntry, ...] = ()
    ahead: int = -1
    behind: int = -1

    @property
    def dirty(self) -> list[str]:
        return [e.line() for e in self.entries if not e.ignored]

    @property
    def work(self) -> list[str]:
        return [e.path for e in self.entries if not e.ignored and not is_cache(e.path)]

    @property
    def ignored(self) -> list[str]:
        return [e.path for e in self.entries if e.ignored and not is_cache(e.path)]


def _count(path: Path, rng: str) -> int:
    r = _W.git(path, "rev-list", "--count", rng)
    return int(r.out) if r.ok and r.out.isdigit() else -1


def tree_work(path: Path, base: str = "", *, ahead: bool = True, behind: bool = False) -> TreeWork:
    """Measure the worktree at ``path`` against ``base`` (see `TreeWork`). ``ahead`` and
    ``behind`` choose which commit counts are asked for; with no ``base`` neither is."""
    r = G.status_run(path, ignored="matching")
    entries = G.parse_status(r)
    n_ahead = _count(path, f"{base}..HEAD") if base and ahead else -1
    n_behind = _count(path, f"HEAD..{base}") if base and behind else -1
    if entries is None:
        why = (r.err or r.out).strip().splitlines() or [f"exit {r.code}"]
        return TreeWork(False, r.err, why[0], (), n_ahead, n_behind)
    return TreeWork(True, "", "", tuple(entries), n_ahead, n_behind)
