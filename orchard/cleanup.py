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

Only ``merged`` is ever acted on without asking, and ``--apply`` additionally merges
``unmerged`` trees whose item the queue already considers complete. Everything else is
described, never performed: the whole point is that the operator (or the agent reading
the report) decides, having been told what is actually in each tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import worktree as W
from .config import Config
from .model import DONE, State


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


def survey(repo: Path, cfg: Config, state: State) -> Plan:
    """Classify every Orchard worktree and branch. Reads only; changes nothing."""
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    prefix = cfg.worktree.branch_prefix
    plan = Plan()

    by_branch = {it.branch: it for it in state.items.values() if it.branch}
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
        item = by_branch.get(branch)
        if item:
            t.item, t.item_state = item.id, item.state
        wt = W.Worktree(item=t.item or t.name, path=Path(path), branch=branch, base=base)
        t.dirty_files = len(W.dirty(wt))
        t.ahead = max(0, W.ahead(wt))
        t.behind = max(0, W.behind(wt))

        if t.dirty_files:
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
        if branch in seen_branches:
            continue
        merged = W.is_merged(root, branch, base)
        t = TreeState(name=branch, branch=branch, kind="stale_branch")
        item = by_branch.get(branch)
        if item:
            t.item, t.item_state = item.id, item.state
        if merged:
            t.action = "delete_branch"
            t.done = f"branch with no worktree, fully merged into {base} — safe to delete"
        else:
            t.done = (
                f"branch with no worktree and commits not on {base}. Its worktree "
                f"was removed without merging; inspect `git log {base}..{branch}`."
            )
        plan.stale_branches.append(t)
    return plan


def apply(repo: Path, cfg: Config, plan: Plan) -> list[str]:
    """Perform only the safe actions. Dirty trees are never touched, whatever is set."""
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    done: list[str] = []
    for t in plan.trees:
        if t.action == "merge":
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
            r = W.remove(repo, cfg, wt)
            done.append(
                f"{'removed' if r.ok else 'kept'} worktree {t.name}"
                + ("" if r.ok else f": {(r.err or r.out).splitlines()[0][:120]}")
            )
    for t in plan.stale_branches:
        if t.action == "delete_branch":
            r = W.git(root, "branch", "-d", t.branch)
            done.append(f"{'deleted' if r.ok else 'kept'} branch {t.branch}")
    return done
