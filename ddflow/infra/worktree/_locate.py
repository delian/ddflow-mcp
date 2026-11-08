"""Where worktrees come from: locate the primary, the current tree, and make scratch ones.

``repo_root``, ``default_branch``, ``current`` and ``Scratch``/``scratch_tree`` live
together because they are all "where is the branch" questions the rest of this package
asks git.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .. import fsio
from .. import git as G
from .. import worktree as _W


@dataclass(frozen=True)
class Scratch:
    """A scratch worktree made by `scratch_tree`, or why there is none."""

    #: The tree; None when git could not make it.
    path: Path | None
    #: The `git worktree add` itself, for its message and exit code.
    add: G.GitResult

    @property
    def error(self) -> str:
        return (self.add.err or self.add.out) if self.path is None else ""


@contextlib.contextmanager
def scratch_tree(
    root: Path | str,
    ref: str,
    *,
    detach: bool = False,
    prefix: str = "ddflow-scratch-",
    under: Path | None = None,
    quiet: bool = False,
    claim: Callable[[Path], Path | None] | None = None,
    deadline_s: float | None = None,
) -> Iterator[Scratch]:
    """A throwaway worktree of ``ref`` in ``root``'s repository: one place that makes it,
    removes it however the body ends and prunes the registry.

    ``under`` is the directory it is made in (the system temp directory by default);
    ``detach`` checks out the commit, not the branch ``ref`` names (git allows a branch in
    one tree only). Yields a `Scratch` whose ``path`` is None when git refused, so the
    caller words its own failure from ``add``. Nothing is left behind: the tree is removed
    (forced), its directory deleted and the registry pruned, in that order, whatever the
    body did.

    ``claim`` runs with the tree's path once it exists but BEFORE `git worktree add`
    registers it (ci passes one that writes its sibling owner marker, so a sweep between
    the add and the claim can never see the tree unclaimed). The path it returns (the
    marker ci writes) is unlinked on exit, so a claim without a registration does not leak.

    ``deadline_s`` bounds the `git worktree add` that checks ``ref`` out: past it git is
    killed and ``add`` reports unavailable (a ``path`` of None); the throwaway tree is
    still removed on exit.
    """
    root = Path(root)
    tmp = fsio.unused_dir(prefix, under)  # `worktree add` wants to create it
    claimed: Path | None = claim(tmp) if claim is not None else None
    args = ["worktree", "add", *(["--quiet"] if quiet else []), *(["--detach"] if detach else [])]
    timeout = {} if deadline_s is None else {"timeout": deadline_s}
    add = _W.git(root, *args, str(tmp), ref, **timeout)
    try:
        yield Scratch(tmp if add.ok else None, add)
    finally:
        if add.ok:
            _W.git(root, "worktree", "remove", "--force", str(tmp))
        # Delete the directory BEFORE pruning: prune only drops registrations whose tree is
        # already gone, so after a failed `remove` it would keep the very entry we clean up.
        if claimed is not None:
            with contextlib.suppress(OSError):
                claimed.unlink()
        fsio.rmtree(tmp)
        _W.git(root, "worktree", "prune")


def _ignore_inside(repo: Path, wt_root: Path) -> None:
    """A worktree root inside the repository ignores itself (`.gitignore` with `*`).

    `.ddflow/.gitignore` lists it too, but a project adopted before the root moved there
    still carries the old file until `init`/`adopt` runs again, and its first claim would
    show a whole checkout as untracked in the main tree (D-worktree-home). A root outside
    the repository needs nothing."""
    try:
        wt_root.resolve().relative_to(Path(repo).resolve())
    except ValueError:
        return
    # A convenience, never a reason to fail a claim: a read-only root still works, and
    # `.ddflow/.gitignore` covers the default location anyway.
    with contextlib.suppress(OSError):
        fsio.ensure_ignored_dir(wt_root, comment="ddflow worktrees: never committed")


def default_branch(repo: Path) -> str:
    """Resolve the repo's default branch. Never assume 'main'.

    Half the repositories in the world are on ``master``; hardcoding either name makes
    the tool silently wrong on the other half. Order: origin/HEAD, then whichever of
    main/master exists, then the current HEAD as a last resort.
    """
    r = _W.git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.ok and r.out:
        return r.out.split("/", 1)[1] if "/" in r.out else r.out
    for cand in ("main", "master"):
        if _W.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{cand}").ok:
            return cand
    r = _W.git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return r.out or "HEAD"


def repo_root(start: Path) -> Path:
    """The PRIMARY checkout, even when called from inside a linked worktree.

    ``--git-common-dir`` points at the shared ``.git`` from any worktree, which is what
    makes every agent land on ONE event log. Using ``--show-toplevel`` instead would
    give each worktree its own log directory — one identity per agent, which is the
    worst possible failure for a coordination system.
    """
    r = _W.git(start, "rev-parse", "--git-common-dir")
    if not r.ok:
        raise G.GitError(f"not a git repository: {start}")
    common = Path(r.out)
    if not common.is_absolute():
        common = (Path(start) / common).resolve()
    return common.parent


def current(start: Path) -> Worktree | None:
    """The LINKED worktree the caller is standing in, or None if it is the primary.

    `--show-toplevel` is the caller's own tree; `repo_root` is the primary. They are
    equal in the primary checkout and differ inside a linked worktree, which is the
    whole discriminator — no name conventions, no path prefixes, nothing that a
    differently-configured harness could defeat.

    This exists because `claim` used to create a worktree unconditionally, so an agent
    whose harness had ALREADY isolated it (Claude Code and Cursor both do) was sent to
    a second tree on a second branch and its uncommitted work was stranded in the
    first. ddflow does not need to have CREATED a tree; it needs to know WHICH tree an
    item is being worked in, so `recover` and `merge` can find it. Being told is as
    good as having made it.

    `base` is left empty: an adopted tree's branch already exists and was not branched
    by us, so claiming to know what it came from would be a guess.

    `created` is False, and that flag alone is NOT what protects the tree. It lives on
    an in-memory `Worktree` that never crosses a process boundary, while
    `remove_on_merge` runs from a later invocation with only the fold to consult — so an
    earlier version of this docstring asserted a safety property that did not exist and
    `merge` deleted the agent's own tree. What protects it is `Item.adopted`, set by the
    `worktree.adopted` handler and checked in `cmd_merge`.
    """
    top = _W.git(start, "rev-parse", "--show-toplevel")
    if not top.ok:
        return None
    try:
        primary = repo_root(start)
    except G.GitError:
        return None
    here = Path(top.out).resolve()
    if here == primary.resolve():
        return None
    head = _W.git(here, "rev-parse", "--abbrev-ref", "HEAD")
    branch = head.out.strip() if head.ok else ""
    if branch == "HEAD":
        # Detached: there is a tree but no branch to merge from later. Reported as no
        # adoptable worktree rather than adopted with an empty branch, because `merge`
        # would then have nothing to act on and would fail at the end of the work
        # instead of at the start.
        return None
    return Worktree(item="", path=here, branch=branch, base="", created=False)


@dataclass
class Worktree:
    item: str
    path: Path
    branch: str
    base: str
    #: The commit ``base`` named when the tree was created, i.e. what the branch
    #: actually forked from. A history rewrite (a secret scrub) later replaces that
    #: commit, so a review diffing against the live ``base`` ref must fall back to this
    #: SHA to see only the item's own work (B05dce361fd).
    base_sha: str = ""
    created: bool = False
    #: git-ignored files copied in from the primary (`[worktree].local_files`)
    local_files: list[str] = field(default_factory=list)
    #: Non-empty when the create-time base sync left an unfinished merge that had to be
    #: aborted (a branch/base conflict): claim surfaces it so the agent never silently
    #: commits a merge it did not choose (B-claim-sync-conflict-silent).
    sync_note: str = ""


def list_worktrees(repo: Path) -> list[dict[str, str]]:
    r = _W.git(repo_root(repo), "worktree", "list", "--porcelain")
    out, cur = [], {}
    for line in r.out.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
        elif " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
        else:
            cur[line] = "true"
    if cur:
        out.append(cur)
    return out
