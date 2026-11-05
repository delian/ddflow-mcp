"""Which tree belongs to whom: the rules `claim`, `merge`, `gate` and `heartbeat` share.

Part of `ddflow.api.lifecycle`; `claim` re-exports these names for its callers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.model import ABANDONED, DONE
from ...infra import worktree as W
from ...services import flow as FS


def _worktree_held_by(st, stored: str, me: str, repo: Path | None = None, cfg=None) -> str:
    """Another OPEN item bound to this same worktree, or "".

    Two items sharing one tree cannot be merged or recovered separately: `merge` would
    take one item's branch for the other's work, and `recover` could not say whose
    uncommitted changes it had found. Closed items are ignored — reusing the tree of
    finished work is exactly what an agent should be able to do. So is an open item
    that has let go of the tree and left nothing in it (`_tree_let_go`), given ``repo``.
    """
    for item in st.items.values():
        if item.id == me or item.removed or item.state in (DONE, ABANDONED):
            continue
        # `item.worktree`, not `item.lease.worktree`: the fold copies the lease's path
        # onto the item and KEEPS it after the lease is released, which is the point -- a
        # released item whose tree still holds its work is exactly the case that must not
        # be silently co-opted.
        if (item.worktree or "") == stored:
            if repo is not None and _tree_let_go(repo, cfg, st, item):
                continue
            return item.id
    return ""


def callers_tree(repo: Path, cfg, st, it, called_from: Path | None) -> tuple[Any, str]:
    """(the linked worktree the caller stands in, or None; the OTHER item working it, or "").

    For an item claimed without a worktree, where the caller stands is the best evidence
    of where its work is -- unless another item is being worked there. Run from item B's
    tree, item A's gate ran B's suite and recorded it as A's pass (found by the critic
    review of B8be9373cf5's fix). Shared by `gate run`/`record`, `review` and `merge`, so
    they cannot disagree about whose tree it is.

    "Being worked" is a LIVE claim: another open item bound to the tree with its lease
    held. Not `claim`'s stricter held-until-let-go rule, which protects a released item's
    leftover work from being co-opted: a tree bound to an item that was merged and let go
    of its lease, carrying newer commits, is exactly where a `--no-worktree` item is
    worked -- the case this feature exists for -- and treating it as foreign made that
    item's own gates unavailable.
    """
    here = W.current(called_from or repo)
    if here is None:
        return None, ""
    stored = W.store_path(repo, here.path)
    for other in st.items.values():
        if other.id == it.id or other.removed or other.state in (DONE, ABANDONED):
            continue
        if (other.worktree or "") == stored and other.lease is not None:
            return None, other.id
    return here, ""


def _tree_let_go(repo: Path, cfg, st, item) -> bool:
    """True when an OPEN item's tree holds none of its work: lease released, merged, and
    nothing in the tree that its merge target lacks -- no uncommitted change, and a HEAD
    the target already contains.

    Open is not the same as occupying. B-release-every-push was merged and released, open
    only for a review the operator deferred, and still refused the next claim in its tree
    (B0ff09a29a5) -- whose way out, `--no-worktree`, then broke `merge` and `review`. An
    expired but unreleased lease still holds: that is `recover`'s, not a claim's.
    """

    if item.lease is not None or not item.merged_sha:
        return False
    tree = W.load_path(repo, item.worktree)
    if not tree.is_dir():
        return True  # no tree, so nothing in it to protect
    if W.dirty(W.Worktree(item=item.id, path=tree, branch=item.branch, base="")):
        return False
    target = FS.target(repo, cfg, item, st)
    return W.git(tree, "merge-base", "--is-ancestor", "HEAD", target).ok


def _tree_of(start: Path) -> Path | None:
    """The git working tree `start` lies in (primary or linked), resolved; None if none.

    The same question `services.enforce._this_worktree` asks of the hook's cwd, asked of
    an explicit start instead: `--repo` is resolved to the primary, so the cwd of this
    process is not where the caller is standing.
    """
    top = W.git(start, "rev-parse", "--show-toplevel", timeout=30)
    return Path(top.out).resolve() if top.ok and top.out else None


def _in_leased_tree(repo: Path, stored: str, here: Path | None) -> bool:
    """Is `here` the tree a lease records? The same test `check_commit` applies."""
    return bool(here and stored and W.load_path(repo, stored).resolve() == here)


def _recorded_tree(repo: Path, it) -> Path | None:
    """The item's own tree from an earlier claim, if it is still on disk; else None.

    Such a tree may hold commits nothing has merged yet. A re-claim must bind to it,
    not to wherever the caller happens to stand, or `merge` merges an empty branch and
    the salvage is orphaned.
    """
    if it is None or not it.worktree or not it.branch:
        return None
    path = W.load_path(repo, it.worktree).resolve()
    return path if _is_items_tree(repo, path, it.branch) else None


def _is_items_tree(repo: Path, path: Path, branch: str) -> bool:
    """Is the tree at `path` still the one that carries `branch`'s work?

    ONE rule for every place a claim would bind an existing tree -- the item's recorded
    tree and the one `W.create` finds at the default path. A directory there is not
    proof: it can have been removed and re-added on something unrelated. Git must list
    it as a worktree of this repository, with `branch` checked out -- or detached (a
    checkout at a commit) with `branch` an ancestor of HEAD, or (a rebase in progress: HEAD
    is on the new base, so the tip is no ancestor) with git recording that it is rebasing
    `branch`. Any other branch, or a detached HEAD holding unrelated work, is someone else's.
    """
    path = Path(path).resolve()
    if not branch or not (path.is_dir() and (path / ".git").exists()):
        return False
    entry = next(
        (e for e in W.list_worktrees(repo) if Path(e.get("worktree", "")).resolve() == path),
        None,
    )
    if entry is None:
        return False
    if entry.get("branch") == f"refs/heads/{branch}":
        return True
    if not entry.get("detached"):
        return False
    ref = f"refs/heads/{branch}"
    if rebasing := _rebasing(path):  # git's own record outranks where HEAD happens to be
        return rebasing == ref
    return W.git(path, "merge-base", "--is-ancestor", ref, "HEAD").ok


def _rebasing(tree: Path) -> str:
    """The ref git is rebasing in ``tree``, or "": git keeps it in the tree's own git dir."""
    for state in ("rebase-merge", "rebase-apply"):
        at = W.git(tree, "rev-parse", "--path-format=absolute", "--git-path", f"{state}/head-name")
        try:
            return Path(at.out).read_text("utf-8").strip() if at.ok else ""
        except OSError:
            continue  # this kind of rebase is not the one running
    return ""
