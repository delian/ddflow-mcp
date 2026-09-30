"""Worktree and branch cleanup — landing work that was left in flight.

The situation this exists for: after a run of interrupted sessions a repository
accumulates worktrees and branches in every state — some fully merged and just not
removed, some carrying commits nobody landed, some holding uncommitted edits, some
belonging to items the queue believes are finished. Left alone they are indistinguishable
from each other, and the safe-looking action (delete the lot) is the one that loses work.

So this module **classifies before it acts**, and the classification is the product:

* ``merged``      — every commit is already on the base branch. Safe to remove.
* ``unmerged``    — carries commits the base branch does not have. Can be merged.
* ``dirty``       — uncommitted edits. NEVER touched automatically; a human looks.
* ``orphan``      — a worktree no queue item claims. Reported with its contents.
* ``stale_branch``— a branch with our prefix and no worktree. Removable if merged.
* ``held``        — its item has a LIVE lease: an agent is working in it now.
* ``adopted``     — the agent harness's own tree (``Item.adopted``), not ddflow's.

``held`` and ``adopted`` are never acted on, whatever git says about them. Git cannot
tell a finished tree from a fresh one: a tree claimed a second ago is clean and nothing
ahead of the base, which is exactly what "merged, safe to remove" looked like -- and
``--apply`` deleted a live agent's tree out from under it (B5a009b185a). Both notions
are ``recover``'s own: ``State.active_leases`` with ``lease.grace_s`` for live, and
``Item.adopted`` for a tree ddflow bound but did not make.

Only ``merged`` is ever acted on without asking, and ``--apply`` additionally merges
``unmerged`` trees whose item the queue already considers complete. Everything else is
described, never performed: the whole point is that the operator (or the agent reading
the report) decides, having been told what is actually in each tree.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Config
from ..core.model import DONE, State, fold
from ..infra import worktree as W

if TYPE_CHECKING:
    from ..infra.log import EventLog


@dataclass
class TreeState:
    name: str
    path: str = ""
    branch: str = ""
    item: str = ""
    kind: str = "orphan"
    ahead: int = 0
    behind: int = 0
    dirty_files: int = 0
    item_state: str = ""
    action: str = ""
    done: str = ""

    def render(self) -> str:
        bits = [f"{self.name:<24} {self.kind:<13}"]
        if self.branch:
            bits.append(f"{self.branch}")
        facts = []
        if self.ahead:
            facts.append(f"{self.ahead} unmerged commit(s)")
        if self.dirty_files:
            facts.append(f"{self.dirty_files} uncommitted file(s)")
        if self.item:
            facts.append(f"item {self.item} is {self.item_state or 'unknown'}")
        line = " ".join(bits) + (f"  [{', '.join(facts)}]" if facts else "")
        return line + (f"\n    -> {self.done or self.action}" if (self.done or self.action) else "")


@dataclass
class Plan:
    trees: list[TreeState] = field(default_factory=list)
    stale_branches: list[TreeState] = field(default_factory=list)

    @property
    def needs_human(self) -> list[TreeState]:
        return [t for t in self.trees if t.kind == "dirty"]

    @property
    def actionable(self) -> list[TreeState]:
        return [t for t in self.trees + self.stale_branches if t.action]


@dataclass
class _Protected:
    """Trees and branches that are someone else's to remove, as ``item -- holder``."""

    held_paths: dict[str, str] = field(default_factory=dict)
    held_branches: dict[str, str] = field(default_factory=dict)
    adopted_paths: dict[str, str] = field(default_factory=dict)

    def why(self, path: str, branch: str) -> tuple[str, str]:
        """(kind, reason) when the tree at ``path`` on ``branch`` must be left alone."""
        who = self.held_paths.get(_key(path)) or (branch and self.held_branches.get(branch))
        if who:
            return "held", (
                f"LEAVE ALONE — item {who} holds a live lease on it: an agent is working "
                f"here now, and a fresh claim looks exactly like finished work."
            )
        who = self.adopted_paths.get(_key(path))
        if who:
            return "adopted", (
                f"LEAVE ALONE — adopted by item {who}: the agent harness's own working "
                f"tree, not ddflow's. The harness removes it, not cleanup."
            )
        return "", ""


def _key(path: str) -> str:
    return str(Path(path).resolve()) if path else ""


def _protected(root: Path, cfg: Config, state: State, now: float | None = None) -> _Protected:
    """What ``recover`` already treats as not-ours: live leases and adopted trees.

    Matched by PATH as well as branch. The item index below is by branch, but an adopted
    tree sits on whatever branch the harness chose, and a lease records the tree it was
    taken on; a branch-only match missed both whenever the two disagreed.
    """
    now = time.time() if now is None else now
    p = _Protected()
    for iid, lease in state.active_leases(now, cfg.lease.grace_s).items():
        it = state.items.get(iid)
        who = f"{iid} (held by {lease.holder or 'unknown'})"
        for stored in (lease.worktree, it.worktree if it else ""):
            if stored:
                p.held_paths[_key(str(W.load_path(root, stored)))] = who
        for br in (lease.branch, it.branch if it else ""):
            if br:
                p.held_branches[br] = who
    for it in state.items.values():
        # Removed and finished items included: the harness's tree outlives ddflow's
        # interest in it, and `merge` keeps an adopted tree for the same reason.
        if it.adopted and it.worktree:
            p.adopted_paths.setdefault(_key(str(W.load_path(root, it.worktree))), it.id)
    return p


def survey(repo: Path, cfg: Config, state: State) -> Plan:
    """Classify every ddflow worktree and branch. Reads only; changes nothing."""
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    prefix = cfg.worktree.branch_prefix
    plan = Plan()
    guard = _protected(root, cfg, state)

    by_branch = {it.branch: it for it in state.items.values() if it.branch}
    by_path = {
        _key(str(W.load_path(root, it.worktree))): it for it in state.items.values() if it.worktree
    }
    seen_branches: set[str] = set()

    for entry in W.list_worktrees(root):
        path = entry.get("worktree", "")
        branch = entry.get("branch", "").replace("refs/heads/", "")
        if not path or Path(path).resolve() == root.resolve():
            continue
        if prefix and not branch.startswith(prefix):
            continue
        seen_branches.add(branch)
        t = TreeState(name=Path(path).name, path=path, branch=branch)
        item = by_branch.get(branch) or by_path.get(_key(path))
        if item:
            t.item, t.item_state = item.id, item.state
        wt = W.Worktree(item=t.item or t.name, path=Path(path), branch=branch, base=base)
        t.dirty_files = len(W.dirty(wt))
        t.ahead = max(0, W.ahead(wt))
        t.behind = max(0, W.behind(wt))

        protected, reason = guard.why(path, branch)
        if protected == "held" or (protected and not t.dirty_files):
            # A held tree's edits are its holder's work in progress, not a question for
            # a human; an adopted DIRTY tree stays `dirty` below, which is what it is.
            t.kind, t.action, t.done = protected, "", reason
        elif t.dirty_files:
            t.kind = "dirty"
            t.action = ""
            t.done = (
                f"LEAVE ALONE — inspect first: `git -C {path} diff {base}`. "
                f"Uncommitted edits are the one thing that exists nowhere else."
            )
        elif t.ahead:
            t.kind = "unmerged"
            if item and item.state == DONE:
                t.action = "merge"
                t.done = (
                    f"the queue considers item {t.item} done but {t.ahead} commit(s) "
                    f"never landed — merge into {base}"
                )
            else:
                t.action = ""
                t.done = (
                    f"{t.ahead} commit(s) not on {base}, and item "
                    f"{t.item or '(none)'} is {t.item_state or 'unknown'}. "
                    f"Finish it, or merge deliberately."
                )
        else:
            t.kind = "merged" if t.item else "orphan"
            t.action = "remove"
            t.done = f"fully merged into {base} — safe to remove"
        plan.trees.append(t)

    for branch in W.branches(root, prefix):
        # The base branch is never "stale": with an empty prefix it was offered for
        # deletion and survived only because the primary had it checked out.
        if branch in seen_branches or branch == base:
            continue
        merged = W.is_merged(root, branch, base)
        t = TreeState(name=branch, branch=branch, kind="stale_branch")
        item = by_branch.get(branch)
        if item:
            t.item, t.item_state = item.id, item.state
        who = guard.held_branches.get(branch)
        if who:
            # `claim` takes the lease before the tree exists, and `--no-worktree` never
            # makes one: a leased branch with no tree is its holder's, not a leftover.
            t.kind = "held"
            t.done = f"LEAVE ALONE — item {who} holds a live lease on this branch."
        elif merged:
            t.action = "delete_branch"
            t.done = f"branch with no worktree, fully merged into {base} — safe to delete"
        else:
            t.done = (
                f"branch with no worktree and commits not on {base}. Its worktree "
                f"was removed without merging; inspect `git log {base}..{branch}`."
            )
        plan.stale_branches.append(t)
    return plan


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
            yield _protected(root, cfg, fold(log.read_all(), strict=False))

    def kept(t: TreeState, guard: _Protected) -> bool:
        kind, reason = guard.why(t.path, t.branch)
        if kind:
            t.action, t.kind, t.done = "", kind, reason
            done.append(f"kept {t.name}: {kind} since the survey")
        return bool(kind)

    for t in plan.trees:
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
            t.action = "remove"
        if t.action == "remove":
            with unless_claimed() as guard:
                if kept(t, guard):
                    continue
                wt = W.Worktree(
                    item=t.item or t.name, path=Path(t.path), branch=t.branch, base=base
                )
                r = W.remove(repo, cfg, wt)
            done.append(
                f"{'removed' if r.ok else 'kept'} worktree {t.name}"
                + ("" if r.ok else f": {(r.err or r.out).splitlines()[0][:120]}")
            )
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
