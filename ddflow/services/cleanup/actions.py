"""The safe actions of the cleanup survey: ``apply`` and its per-tree/scratch helpers.

``apply`` performs only the safe actions from a ``Plan`` -- every removal and branch
deletion re-reads the queue under the log's lock. Split out of
``ddflow/services/cleanup.py`` (rule r-small-files); it sits above ``dispose``/``survey``
and builds on ``model``/``stranded``.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from ...config import Config
from ...core.model import fold
from ...infra import fsio
from ...infra import worktree as W
from ...infra.log import EventLog
from .. import ci as CI
from ..leaseclock import localize
from ..pins import pin_files
from .model import Disposal, Plan, TreeState, _Protected
from .plan import _owner_alive
from .stranded import _protected


def _registered(root: Path, tree: Path) -> bool:
    """Is ``tree`` still a registration in git's worktree list of ``root``? The one prune
    asks: removing the directory is not the same as removing the registration, and a prune
    that refuses ONE registered path (a lock) says success either way."""
    return any(
        Path(e.get("worktree", "")).resolve() == Path(tree).resolve()
        for e in W.list_worktrees(root)
    )


def _pruned_scratch(log: EventLog, root: Path, t: TreeState, done: list[str]) -> bool:
    """Free one leftover scratch tree whose owner is gone (B43593d1f3c): the owner marker
    re-read under the log's lock (the owner could have re-started since the survey), and a
    half-removal reported as such -- a prune that could not free the directory or its
    registration said it pruned is a leftover the next survey would find again."""
    with log.transaction():
        verified = _owner_alive(Path(t.path))
        if verified is True:
            t.action, t.done = "", "kept: its owner process is alive since the survey"
            done.append(f"kept {t.name}: its owner process is alive since the survey")
            return False
        if verified is None and CI.owner_marker(Path(t.path)).exists():
            t.action, t.done = "", "kept: its owner process cannot be checked since the survey"
            done.append(f"kept {t.name}: its owner could not be checked since the survey")
            return False
        # The directory BEFORE the prune: a prune only drops a registration whose tree is
        # already gone, so a half-freed directory would keep its entry to find again.
        fsio.rmtree_owned(t.path)
        try:
            CI.owner_marker(Path(t.path)).unlink(missing_ok=True)
        except OSError:
            pass
        W.git(root, "worktree", "prune")
    if Path(t.path).exists() or _registered(root, Path(t.path)):
        t.action, t.done = "", "prune FAILED — still present or still registered; named as such"
        done.append(f"FAILED to prune leftover ci scratch tree {t.name}")
        return False
    t.action, t.done = "", "pruned: gone, and its git registration is gone"
    done.append(f"pruned leftover ci scratch tree {t.name}")
    return True


def _remove_planned(repo: Path, cfg: Config, log: EventLog, t: TreeState, base: str) -> list[str]:
    """Carry out one planned ``remove``; the lines for the report (and ``t`` updated when kept).

    A tree with commits ahead is only here as a landed item's merge/pin leftovers
    (Bfc4ac64c9c): re-proved against the queue as it is now, then removed with that proof."""
    # late-bound: honour monkeypatch of the package attr
    _dispose_tree = sys.modules[__package__].dispose_tree
    _proved_leftover_tip = sys.modules[__package__].proved_leftover_tip

    root = W.repo_root(repo)
    wt = W.Worktree(item=t.item or t.name, path=Path(t.path), branch=t.branch, base=base)
    proof = tip = ""
    if t.ahead:
        it = fold(log.read_all(), strict=False).items.get(t.item)
        tip = _proved_leftover_tip(t.path, it, base, pin_files(repo, cfg))
        if not tip:
            t.action, t.kind = "", "unmerged"
            t.done = f"kept: {t.name} is no longer only landed leftovers since the survey"
            return [f"kept {t.name}: no longer only landed leftovers"]
        work = W.tree_work(Path(t.path))
        moved = W.git(Path(t.path), "rev-parse", "--verify", "HEAD").out.strip() != tip
        if not work.readable:
            t.action, t.kind = "", "unreadable"
            t.done = f"LEAVE ALONE — could not measure {t.name} since the survey ({work.msg[:120]})"
            return [f"kept {t.name}: could not be measured since the survey"]
        if work.dirty or moved:
            t.action, t.kind = "", "unmerged"
            t.done = f"kept: {t.name} changed since it was proved to be only leftovers"
            return [f"kept {t.name}: the tree changed since it was proved to be leftovers"]
        proof = f"{t.item} landed; only merge-main/pin commits left"
    d = _dispose_tree(repo, cfg, log, wt, landed_tip=tip)
    if proof and tip and d.removed and d.branch_left:
        # The branch only lacks the merge-main/pin commits just proved to be leftovers. Dropped
        # under the log's lock, still held by nobody, and only if it is still at the proved tip.
        with log.transaction():
            guard = _protected(
                root, cfg, localize(fold(log.read_all(), strict=False), log, time.time())
            )
            if (
                not guard.held_branches.get(t.branch)
                and W.git(root, "update-ref", "-d", f"refs/heads/{t.branch}", tip).ok
            ):
                d = Disposal("removed", had_branch=True)
    if d.outcome == "refused":
        t.action, t.kind, t.done = "", d.kind, d.why
        return [f"kept {t.name}: {d.kind} since the survey"]
    return [
        f"{'removed' if d.removed else 'kept'} worktree {t.name}"
        + ("" if d.removed else f": {d.why.splitlines()[0][:120]}")
    ]


def apply(repo: Path, cfg: Config, plan: Plan, log: EventLog) -> list[str]:
    """Perform only the safe actions. Dirty trees are never touched, whatever is set.

    Every removal and branch deletion re-reads the queue from ``log`` and acts INSIDE
    the log's append lock, the one `claim` takes to record a lease. The plan is as old as
    the survey, which ran git in every tree, and a merge here takes seconds more, so a
    tree claimed meanwhile has to be seen. Re-reading alone was not enough: a claim
    appended between the re-read and `git worktree remove` still lost its tree
    (B19d87ac436). Under the lock a claim either landed first, and is seen, or waits and
    then finds no tree and makes a fresh one. ``log`` is required, not optional: a
    sweep with no way to re-check has nothing but a stale plan to go on.

    A ``merge`` action is checked before it too, but runs outside the lock: it only lands
    commits on the base and touches no tree. The removal that follows it re-enters the
    lock and checks again, like any other.
    """
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    done: list[str] = []

    @contextlib.contextmanager
    def unless_claimed() -> Iterator[_Protected]:
        with log.transaction():
            yield _protected(
                root, cfg, localize(fold(log.read_all(), strict=False), log, time.time())
            )

    def kept(t: TreeState, guard: _Protected) -> bool:
        kind, reason = guard.why(t.path, t.branch)
        if kind:
            t.action, t.kind, t.done = "", kind, reason
            done.append(f"kept {t.name}: {kind} since the survey")
        return bool(kind)

    for t in plan.trees:
        if t.action == "prune_scratch" and _pruned_scratch(log, root, t, done):
            continue
        if t.action == "merge":
            with unless_claimed() as guard:
                if kept(t, guard):
                    continue
            wt = W.Worktree(item=t.item or t.name, path=Path(t.path), branch=t.branch, base=base)
            r = W.merge(repo, cfg, wt, message=f"merge {t.item or t.branch} (cleanup)")
            done.append(
                f"{'merged' if r.ok else 'FAILED to merge'} {t.branch}"
                + ("" if r.ok else f": {(r.err or r.out).splitlines()[0][:120]}")
            )
            if not r.ok:
                continue
            t.action, t.ahead = "remove", 0  # merged: nothing is ahead of the base any more
        if t.action == "remove":
            done.extend(_remove_planned(repo, cfg, log, t, base))
    for t in plan.stale_branches:
        if t.action == "delete_branch":
            with unless_claimed() as guard:
                who = guard.held_branches.get(t.branch)
                if who:
                    t.action, t.kind = "", "held"
                    t.done = f"LEAVE ALONE — item {who} holds a live lease on this branch."
                    done.append(f"kept branch {t.branch}: held since the survey")
                    continue
                r = W.git(root, "branch", "-d", t.branch)
            done.append(f"{'deleted' if r.ok else 'kept'} branch {t.branch}")
    return done
