"""The read-only survey: classify every ddflow worktree and branch, change nothing.

``survey`` is the product before any action -- merged/unmerged/dirty/unreadable/orphan/
stale, per tree, measured against git and the live leases. Split out of
``ddflow/services/cleanup.py`` (rule r-small-files); the matching ``apply`` consumes its
``Plan``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

from ...config import Config
from ...core.flow import GITFLOW
from ...core.globs import under_any
from ...core.model import DONE, Item, State
from ...infra import fsio
from ...infra import worktree as W
from .. import ci as CI
from ..pins import pin_files
from .model import Plan, TreeState, _key, _Protected
from .stranded import _clone_of, _own_released, _protected, stranded_leases


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


def proved_leftover_tip(path: str, item: Item | None, base: str, pins: list[str]) -> str:
    """Did ``item`` land on ``base`` (its recorded ``merged_sha`` is reachable from it), with
    only merge-main commits and pin regenerations left on the tree (Bfc4ac64c9c)?

    A crash after the merge gate leaves the item abandoned and its tree a few such commits
    ahead of the base: none of it is work. A merge commit counts only when every parent
    beyond the first is already on the base; any other commit only when it touches nothing
    but pin files. Returns the commit that was proved, so a later step can be bound to exactly
    it; "" for anything else, and for unknown (git failing, no recorded merge)."""
    if not item or not item.merged_sha:
        return ""
    here = Path(path)
    head = W.git(here, "rev-parse", "--verify", "HEAD").out.strip()
    if not head:
        return ""
    if not W.git(here, "merge-base", "--is-ancestor", item.merged_sha, base).ok:
        return ""
    log = W.git(here, "log", "--format=%H %P", f"{base}..{head}")
    if not log.ok:
        return ""
    for line in log.out.splitlines():
        sha, *parents = line.split()
        merge = len(parents) > 1
        if merge and not all(
            W.git(here, "merge-base", "--is-ancestor", p, base).ok for p in parents[1:]
        ):
            return ""
        # A merge's own content is what differs from EVERY parent (`--cc`): a conflict
        # resolution or an edit made inside the merge, which exists on no other commit.
        flags = ("--cc",) if merge else ()
        files = W.git(
            here, "diff-tree", "--root", *flags, "-z", "--no-commit-id", "--name-only", "-r", sha
        )
        names = [f for f in files.out.split("\0") if f] if files.ok else []
        # a non-merge commit that touches nothing (--allow-empty) is someone's note, not a pin run
        if not files.ok or (not merge and not names) or not all(under_any(f, pins) for f in names):
            return ""
    return head


def landed_leftovers_only(path: str, item: Item | None, base: str, pins: list[str]) -> bool:
    """`proved_leftover_tip` as a yes/no: is everything the tree has ahead of ``base`` a landed
    item's merge-main/pin leftover?"""
    return bool(proved_leftover_tip(path, item, base, pins))


def _classify(
    t: TreeState,
    item: Item | None,
    unknown: str,
    base: str,
    path: str,
    pins: list[str],
) -> None:
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
    elif t.ahead and landed_leftovers_only(path, item, base, pins):
        t.kind = "merged"
        t.action = "remove"
        t.done = (
            f"item {t.item} landed on {base} ({item.merged_sha[:8] if item else ''}); the "
            f"{t.ahead} commit(s) left are merge-main/pin commits only — safe to remove"
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


def _owner_alive(tree: Path) -> bool | None:
    """Whether the owner of ci scratch tree ``tree`` is what its marker says: True (a
    process ``pid`` that started at the marker's start-ticks lives), False (it has
    DEFINITELY exited: the pid is gone, or re-started -- a reused pid is not the owner),
    None (unknown: no marker, so the tree predates owner recording, most likely a SIGKILLed
    run; or a pid THIS host cannot read)."""
    _Path = sys.modules[__package__].Path  # late-bound: honours monkeypatch of the package attr

    try:
        text = CI.owner_marker(tree).read_text().strip()
        pid_s, _, start = text.partition(" ")
    except OSError:
        return None
    if not pid_s.isdigit() or not start.isdigit():
        return None
    proc = _Path(f"/proc/{pid_s}")
    try:
        raw = _Path(f"{proc}/stat").read_text("utf-8", "replace").rsplit(")", 1)[1].split()
        if not raw:
            return None
        if raw[0] == "Z":  # a zombie has exited: it was read but never reaped, not alive
            return False
        return raw[19] == start
    except PermissionError:
        # EACCES/EPERM: a /proc that hides this pid (hidepid=, another UID) serves NOTHING
        # about whether it lives: unknown, never a prune.
        return None
    except FileNotFoundError:
        # No /proc/<pid> while /proc itself serves: the pid is gone -- DEFINITELY exited.
        # (The ci run and this sweep share a uid, so a live owner's pid is never ENOENT.)
        return False if _Path("/proc").exists() else None
    except (OSError, IndexError):
        return None


def _under_sys_tmp(path: Path) -> bool:
    """Is ``path`` in the directory a ci scratch tree is MADE in (the system temp dir)?
    The one place a markerless ``ddflow-ci.*`` tree is known to be ci's: anywhere else the
    name is just a name, and its tree is someone's (not ci's) to remove."""
    try:
        return path.parent.resolve() == fsio.temp_root().resolve()
    except OSError:
        return False


def _scratch_row(tree: Path, alive: bool | None) -> tuple[str, str]:
    """(action, the sentence) for a worktree a ci run's scratch helper made, kept or
    pruned by ``apply``: only an owner seen to be GONE is a leftover; a live owner's, and
    one whose owner cannot be checked, stay and are only named. A markerless tree is the
    pre-recording leftover the sweep exists for ONLY in the temp dir, where a ci scratch
    tree is made; elsewhere a markerless ``ddflow-ci.*`` is a name, not a claim to it."""
    if alive:
        return "", "a live ci run's scratch tree — its owner process is still running"
    tail = " — prune it and its git registration"
    if alive is False:
        return "prune_scratch", f"a ci run's scratch tree whose owner process is gone{tail}"
    if not CI.owner_marker(tree).exists():
        if _under_sys_tmp(tree):
            return "prune_scratch", (
                f"a ci run's scratch tree (no owner marker: from before owners were recorded){tail}"
            )
        return "", (
            "LEFT ALONE — named like a ci scratch tree but made before owners were "
            "recorded outside the temp dir: its worktree is not ci's to remove"
        )
    return "", "a ci run's scratch tree whose owner process cannot be checked — LEFT ALONE"


def scratch_leftovers(root: Path) -> list[TreeState]:
    """The registered worktrees that are NOT the queue's: a ci run's detached scratch
    elsewhere (`ddflow-ci.`), which `our_trees` never sees because it is on no ddflow
    branch AND under no ddflow tree root. A run killed mid-flight (no exit cleanup, SIGKILL
    runs no handler) left the tree and its registration; these stay registered until
    named here and pruned by ``apply``."""
    out: list[TreeState] = []
    for entry in W.list_worktrees(root):
        path = entry.get("worktree", "")
        full = Path(path)
        if not path or not full.name.startswith(CI.SCRATCH_PREFIX):
            continue
        t = TreeState(name=full.name, path=path, branch=entry.get("branch", ""), kind="scratch")
        t.action, t.done = _scratch_row(full, _owner_alive(full))
        out.append(t)
    return out


def _stale_branch(
    root: Path, branch: str, base: str, guard: _Protected, own: dict[str, str], by_branch: dict
) -> TreeState:
    """Classify a branch of ours that no worktree is on."""
    merged = W.is_merged(root, branch, base)
    t = TreeState(name=branch, branch=branch, kind="stale_branch")
    item = by_branch.get(branch)
    if item:
        t.item, t.item_state = item.id, item.state
    who = guard.held_branches.get(branch)
    if who:
        # `claim` takes the lease before the tree exists, and `--no-worktree` never
        # makes one: a leased branch with no tree is its holder's, not a leftover.
        t.kind = "stranded" if branch in guard.stranded else "held"
        t.done = (
            guard.stranded.get(branch)
            or own.get(branch)
            or f"LEAVE ALONE — item {who} holds a live lease on this branch."
        )
    elif merged:
        t.action = "delete_branch"
        t.done = f"branch with no worktree, fully merged into {base} — safe to delete"
    else:
        t.done = (
            f"branch with no worktree and commits not on {base}. Its worktree "
            f"was removed without merging; inspect `git log {base}..{branch}`."
        )
    return t


def survey(repo: Path, cfg: Config, state: State) -> Plan:
    """Classify every ddflow worktree and branch. Reads only; changes nothing."""
    root = W.repo_root(repo)
    base = cfg.worktree.base_ref or W.default_branch(root)
    prefixes = our_prefixes(cfg)
    plan = Plan()
    guard = _protected(root, cfg, state)
    own = _own_released(repo, cfg)
    pins = pin_files(repo, cfg)
    for x in stranded_leases(root, cfg, state, _clone_of(repo, cfg)):
        if x.branch:
            guard.stranded[x.branch] = x.render()
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
        if protected == "held" and branch in own:
            reason = own[branch]
        if protected in ("held", "stranded") or (protected and not t.dirty_files and not unknown):
            # A held tree's edits are its holder's work in progress, not a question for
            # a human; an adopted DIRTY tree stays `dirty` below, which is what it is.
            t.kind, t.action, t.done = protected, "", reason
        else:
            _classify(t, item, unknown, base, path, pins)
        plan.trees.append(t)

    plan.trees.extend(scratch_leftovers(root))

    for branch in W.branches(root):
        if not _ours(branch, prefixes):
            continue
        # The base branch is never "stale": with an empty prefix it was offered for
        # deletion and survived only because the primary had it checked out.
        if branch in seen_branches or branch == base:
            continue
        plan.stale_branches.append(_stale_branch(root, branch, base, guard, own, by_branch))
    return plan
