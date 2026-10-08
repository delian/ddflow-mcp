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
* ``unreadable``  — git could not read it or count its commits. Unknown is never clean:
                    NEVER touched automatically; a human looks.
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
from ..core.flow import GITFLOW
from ..core.model import DONE, Item, State, fold
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
        return [t for t in self.trees if t.kind in ("dirty", "unreadable")]

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


def _protected(
    root: Path, cfg: Config, state: State, now: float | None = None, *, ignore: str = ""
) -> _Protected:
    """What ``recover`` already treats as not-ours: live leases and adopted trees.

    ``ignore`` is an item whose own lease does not count: the item whose tree the caller is
    disposing of holds it, and "held by the item being merged" is not a reason to keep it.

    Matched by PATH as well as branch. The item index below is by branch, but an adopted
    tree sits on whatever branch the harness chose, and a lease records the tree it was
    taken on; a branch-only match missed both whenever the two disagreed.
    """
    now = time.time() if now is None else now
    p = _Protected()
    for iid, lease in state.active_leases(now, cfg.lease.grace_s).items():
        if iid == ignore:
            continue
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


def record_item_removed(log: EventLog, it) -> None:
    """THE writer of `worktree.removed` (B54909478c4): the item's tree as `claim` recorded
    it (`it.worktree`, often repo-relative), whoever removed it -- merge, the PR flow or
    cleanup -- so a change to the event is made in one place."""
    log.append("worktree.removed", it.id, {"path": it.worktree})


def record_removed(log: EventLog, root: Path, path: str) -> None:
    """`worktree.removed` for every item whose recorded tree is ``path``, as `merge`
    writes it, so the fold stops pointing at a directory that is gone (B5e83fb22cb).
    Call it under ``log.transaction()``, right after the removal."""
    gone = _key(path)
    for it in fold(log.read_all(), strict=False).items.values():
        if it.worktree and _key(str(W.load_path(root, it.worktree))) == gone:
            record_item_removed(log, it)


@dataclass(frozen=True)
class Disposal:
    """What `dispose_tree` did: ``outcome`` is "removed", "refused" (the tree is someone's:
    ``kind`` says whose, "held" or "adopted") or "failed" (git said no, or the tree holds
    work and was not forced); ``why`` is the sentence for the one who asked. A removal that
    worked but could not delete the branch has ``branch_left`` set to git's answer."""

    outcome: str
    why: str = ""
    kind: str = ""
    branch_left: str = ""
    had_branch: bool = False

    @property
    def removed(self) -> bool:
        return self.outcome == "removed"


def dispose_tree(
    repo: Path,
    cfg: Config,
    log: EventLog | None,
    wt: W.Worktree,
    *,
    item: Item | None = None,
    forced_by: str = "",
) -> Disposal:
    """THE removal of a worktree (B-uni-tree-lifecycle): merge, the PR flow, cleanup and
    onboarding all end here, so every one is guarded, serialised with the claims and logged.

    * **Under the log's lock, with the leases re-read**: a tree a LIVE lease holds, or an
      adopted one, is refused (``item``'s own lease -- the caller holds it -- does not count).
      A claim appended between a survey and this call is seen, or waits and then finds no tree.
    * **Guarded by git**: without ``forced_by``, `W.remove` refuses a tree with uncommitted
      files, unmerged commits, or one it could not measure. ``forced_by`` is the caller's
      PROOF that removing whatever the tree holds is safe (the PR head merged on the forge;
      an inspection that found nothing beyond caches): it makes the removal forced, and the
      caller measured the tree itself.
    * **Logged**: a removal that worked appends `worktree.removed` -- for ``item`` when given,
      else for every item whose recorded tree is this path (B5e83fb22cb).

    ``log`` None is a project that keeps no event log: no lock, no guard, nothing to record.
    """
    root = W.repo_root(repo)
    with log.transaction() if log else contextlib.nullcontext():
        if log is not None:
            guard = _protected(
                root, cfg, fold(log.read_all(), strict=False), ignore=item.id if item else ""
            )
            kind, reason = guard.why(str(wt.path), wt.branch)
            if kind:
                return Disposal("refused", reason, kind)
        had_branch = bool(wt.branch) and W.branch_exists(root, wt.branch)
        r = W.remove(repo, cfg, wt, force=bool(forced_by))
        if not r.ok:
            return Disposal("failed", (r.err or r.out).strip() or f"git exit {r.code}")
        if log is not None:
            if item is not None:
                record_item_removed(log, item)
            else:
                record_removed(log, root, str(wt.path))
    if had_branch and W.branch_exists(root, wt.branch):
        rb = W.git(root, "branch", "-d", wt.branch)
        if not rb.ok:
            left = (rb.err or rb.out).strip() or f"git exit {rb.code}"
            return Disposal("removed", branch_left=left, had_branch=True)
    return Disposal("removed", had_branch=had_branch)


def our_prefixes(cfg: Config) -> list[str]:
    """Branch prefixes that mark a branch as ddflow's: ``worktree.branch_prefix``, plus
    the feature/bugfix/hotfix prefixes when the project runs gitflow (B177), where
    ``branch_name`` creates task branches under those instead.

    ``release_prefix`` is deliberately NOT here: a release branch can be a long-lived
    line, and one with nothing ahead of the base looks "merged" to git. An EMPTY gitflow
    prefix is dropped, not read as "every branch is ours" (``branch_prefix`` keeps its
    historical empty-means-all meaning)."""
    out = [cfg.worktree.branch_prefix]
    if cfg.flow.model == GITFLOW:
        out += [
            p
            for p in (cfg.flow.feature_prefix, cfg.flow.bugfix_prefix, cfg.flow.hotfix_prefix)
            if p
        ]
    return out


def is_ours(branch: str, cfg: Config) -> bool:
    return _ours(branch, our_prefixes(cfg))


def _ours(branch: str, prefixes: list[str]) -> bool:
    return any(not p or branch.startswith(p) for p in prefixes)


def _measure(t: TreeState, wt: W.Worktree) -> str:
    """Fill ``t``'s counts from git; why it could not be measured, or "" if it could.

    Unknown is never clean: a tree git could not read, or whose commits could not be
    counted, is not "fully merged" however empty it looks (B028b11b4cb).
    """
    tw = W.tree_work(wt.path, wt.base, behind=True)
    t.ahead = max(0, tw.ahead)
    t.behind = max(0, tw.behind)
    if not tw.readable:
        return f"git status failed: {tw.msg}"
    t.dirty_files = len(tw.dirty)
    return "" if tw.ahead >= 0 else f"cannot count commits ahead of {wt.base}"


def _classify(t: TreeState, item: Item | None, unknown: str, base: str, path: str) -> None:
    """Kind, action and advice for a tree nobody holds or adopted cleanly."""
    if unknown:
        t.kind = "unreadable"
        t.action = ""
        t.done = (
            f"LEAVE ALONE — could not measure it ({unknown[:120]}). "
            f"Inspect first: `git -C {path} status` and `git -C {path} log {base}..HEAD`."
        )
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


def _claimed_by(root: Path, state: State) -> tuple[dict[str, Item], dict[str, Item]]:
    """The queue's side of "who owns this tree": items by branch and by normalised path."""
    by_branch = {it.branch: it for it in state.items.values() if it.branch}
    by_path = {
        _key(str(W.load_path(root, it.worktree))): it for it in state.items.values() if it.worktree
    }
    return by_branch, by_path


def our_trees(root: Path, cfg: Config) -> Iterator[tuple[str, str]]:
    """``(path, branch)`` of every linked worktree on one of ddflow's branches: THE test for
    "is this tree ddflow's" that the survey and doctor share. The primary checkout is never
    one."""
    prefixes = our_prefixes(cfg)
    for entry in W.list_worktrees(root):
        path = entry.get("worktree", "")
        branch = entry.get("branch", "").replace("refs/heads/", "")
        if not path or Path(path).resolve() == root.resolve():
            continue
        if _ours(branch, prefixes):
            yield path, branch


def unclaimed_trees(repo: Path, cfg: Config, state: State) -> list[str]:
    """Paths of our worktrees that no item claims, by branch or by normalised path. The
    tree-side orphan detector, without the git measuring ``survey`` does."""
    root = W.repo_root(repo)
    by_branch, by_path = _claimed_by(root, state)
    return [
        path
        for path, branch in our_trees(root, cfg)
        if not (by_branch.get(branch) or by_path.get(_key(path)))
    ]


def survey(repo: Path, cfg: Config, state: State) -> Plan:
    """Classify every ddflow worktree and branch. Reads only; changes nothing."""
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    prefixes = our_prefixes(cfg)
    plan = Plan()
    guard = _protected(root, cfg, state)
    by_branch, by_path = _claimed_by(root, state)
    seen_branches: set[str] = set()

    for path, branch in our_trees(root, cfg):
        seen_branches.add(branch)
        t = TreeState(name=Path(path).name, path=path, branch=branch)
        item = by_branch.get(branch) or by_path.get(_key(path))
        if item:
            t.item, t.item_state = item.id, item.state
        wt = W.Worktree(item=t.item or t.name, path=Path(path), branch=branch, base=base)
        unknown = _measure(t, wt)

        protected, reason = guard.why(path, branch)
        if protected == "held" or (protected and not t.dirty_files and not unknown):
            # A held tree's edits are its holder's work in progress, not a question for
            # a human; an adopted DIRTY tree stays `dirty` below, which is what it is.
            t.kind, t.action, t.done = protected, "", reason
        else:
            _classify(t, item, unknown, base, path)
        plan.trees.append(t)

    for branch in W.branches(root):
        if not _ours(branch, prefixes):
            continue
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
            wt = W.Worktree(item=t.item or t.name, path=Path(t.path), branch=t.branch, base=base)
            d = dispose_tree(repo, cfg, log, wt)
            if d.outcome == "refused":
                t.action, t.kind, t.done = "", d.kind, d.why
                done.append(f"kept {t.name}: {d.kind} since the survey")
                continue
            done.append(
                f"{'removed' if d.removed else 'kept'} worktree {t.name}"
                + ("" if d.removed else f": {d.why.splitlines()[0][:120]}")
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
