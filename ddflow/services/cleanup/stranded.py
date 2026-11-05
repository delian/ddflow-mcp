"""Lease-vs-removed reasoning: what ``recover`` already treats as not-ours.

The functions here answer "whose is this, and is it stranded?" from the item index and
the live leases alone, before git is measured. They are ``survey``'s and ``dispose``'s
inputs, and their product (``_Protected``) is the guard every removal re-checks under the
log's lock. Split out of ``ddflow/services/cleanup.py`` (rule r-small-files).
"""

from __future__ import annotations

import time
from pathlib import Path

from ...config import Config
from ...core.model import State
from ...infra import worktree as W
from ...infra.log import EventLog
from .. import leases as L
from .model import Stranded, _key, _Protected, stranded_text


def removed_with_live_lease(
    state: State, cfg: Config, mine: str | None, now: float | None = None
) -> list[dict[str, str]]:
    """Every removed item whose lease is still live, in ANY clone: `status` lists them, in the
    clone that removed the item as well as the holder's (fix-removed-item-residuals)."""
    now = time.time() if now is None else now
    return [
        {
            "item": iid,
            "holder": ls.holder,
            "clone": ls.clone,
            "text": stranded_text(
                iid, ls.holder, ls.branch or state.items[iid].branch, ls.worktree, ls.clone, mine
            ),
        }
        for iid, ls in sorted(state.active_leases(now, cfg.lease.grace_s).items())
        if state.items[iid].removed
    ]


def stranded_leases(
    root: Path, cfg: Config, state: State, mine: str = "", now: float | None = None
) -> list[Stranded]:
    """Removed items whose LIVE lease still points at a branch or tree that exists in THIS
    clone: the holder's work, unreachable by merge/complete (B71dedb76d2). A removed item
    with no live lease, or whose branch and tree are not here (the clone that removed it),
    strands nothing."""
    now = time.time() if now is None else now
    out: list[Stranded] = []
    for iid, lease in sorted(state.active_leases(now, cfg.lease.grace_s).items()):
        it = state.items.get(iid)
        if it is None or not it.removed or L.foreign(lease, mine):
            # Another clone's lease strands THAT clone's branch, not any branch of the same
            # name here (a loser's stale one, say): the holder's clone reports it.
            continue
        branch = lease.branch or it.branch
        tree = lease.worktree or it.worktree
        here = bool(branch and W.branch_exists(root, branch)) or bool(
            tree and W.load_path(root, tree).exists()
        )
        if here:
            out.append(Stranded(iid, lease.holder, branch, tree))
    return out


def _own_released_branches(lost: dict[str, dict]) -> dict[str, str]:
    """branch -> the sentence for it, for each claim this agent lost to a contest.

    The branch the LOSING claim bound, never the item's: that is the winner's now."""
    return {
        info["branch"]: (
            f"LEAVE ALONE — this is your own, released branch: your claim on {iid} lost "
            f"the contest to {info['winner'] or 'another holder'} (`ddflow show {iid}` says "
            f"who holds it now). Its commits are not on the base: keep them with git or "
            f"port them."
        )
        for iid, info in lost.items()
        if info["branch"]
    }


def _clone_of(repo: Path, cfg: Config) -> str:
    """This clone's id, "" when unadopted or unreadable (creates nothing)."""
    try:
        return EventLog(repo, log_cfg=cfg.log).clone_id(create=False)
    except OSError:
        return ""


def _own_released(repo: Path, cfg: Config) -> dict[str, str]:
    """branch -> why it is NOT another's, for the branches of claims THIS agent lost to a
    contest (B2809f4213b): the lease that now holds the item is the winner's, and calling
    the loser's own branch 'held by' the winner told him nothing about what to do with it."""
    try:
        log = EventLog(repo, log_cfg=cfg.log)
        return _own_released_branches(L.lost_contests(log, cfg.agent.id or log.agent_id))
    except OSError:
        return {}


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
