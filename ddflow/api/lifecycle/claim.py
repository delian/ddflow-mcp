"""`claim`: lease an item and give it a worktree; the claim refusals.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, NamedTuple

from ...config import csv_list
from ...core import globspec as GS
from ...core import outcome as O
from ...core import progress as PR
from ...core.model import ABANDONED, DONE, REVIEW, fold
from ...core.schedule import needs_tree
from ...infra import worktree as W
from ...services import choices as CHO
from ...services import flow as FS
from ...services import leases as L
from ...services import ports as PT
from ...services import waits as WT
from ...services.guidance import inject as GI
from .._base import _load
from ..reporting import new_reports
from .planning import alternatives_offer
from .reservations import _reserved_for, _reserved_msg


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
        return False
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
    rebase in progress, a checkout at a commit) with `branch` an ancestor of HEAD. Any
    other branch, or a detached HEAD holding unrelated work, is someone else's tree.
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
    return bool(
        entry.get("detached")
        and W.git(path, "merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD").ok
    )


def _undo_claim(log, item: str, held_before: bool, note: str) -> None:
    """Take back what a refused claim acquired -- and ONLY that.

    A fresh claim that is refused releases its lease, or the refusal itself creates the
    stuck claim `recover` exists to clean up. But a caller that already held a live lease
    on the item got a renewal, not a new lease: releasing it would hand an item whose
    tree holds that caller's work to anyone who asks.
    """
    if not held_before:
        L.release(log, item, note=note)


def _bring_local_files(repo: Path, cfg, wt: W.Worktree | None) -> None:
    """`[worktree].local_files` into the tree a claim bound, however it was bound.

    Not only `W.create`'s tree: an adopted harness tree and the item's own tree rebound
    on a re-claim are checkouts too, missing the same git-ignored files (bug
    B41902e229d). The copy never overwrites, so a second pass over a tree is harmless.
    """
    if wt is not None and not wt.local_files:
        wt.local_files = W.copy_local_files(repo, wt.path, cfg.worktree.local_files)


class _Bound(NamedTuple):
    """What a claim bound for the item: the tree (None for none) and how it came to it."""

    wt: W.Worktree | None = None
    ours: bool = False  # a tree ddflow made (now or on an earlier claim), not one it adopted
    rebound: bool = False  # bound to the item's OWN recorded tree from an earlier claim
    was_adopted: bool = False


def claim(
    repo: Path,
    item: str,
    *,
    globs: str = "",
    note: str = "",
    force: bool = False,
    no_worktree: bool = False,
    called_from: Path | None = None,
    resources: str = "",
    agent: str = "",
) -> O.Outcome:
    """Acquire a lease and (optionally) bind a worktree. Exit 3 if refused.

    Refuses an item that is already looping when `[loops].on_detect = "block"`. That
    refusal is the only thing that actually stops an agent spinning: a warning in a report
    is read by a human later, while a refused claim is read by the agent now.
    """

    log, cfg, _ = _load(repo, agent)
    events = log.read_all()
    st = fold(events, strict=False)
    parked = st.items.get(item)
    no_worktree = no_worktree or (
        parked is not None and not needs_tree(parked)
    )  # `no-worktree` tag
    if parked is not None and parked.state == REVIEW and not force:
        # Claiming it would let a push ride into the request its reviewers already
        # judged -- and while it ran, `pr sync` would stop watching the request at all.
        return O.refused(
            "item.claimed",
            f"{item} is in review ({parked.pr.url if parked.pr else 'its request'}); the "
            f"reviewers have it. `ddflow pr sync` brings it back if they request changes. "
            f"--force to take it back anyway.",
            id=item,
            alternatives=[],
        )
    # One reader for every surface: a repeated flag's values, or a JSON array sent as one
    # string over MCP, read whole -- split on commas it was refused as mangled.
    want = GS.parse(globs) or None
    # A holder re-claiming its own LIVE lease only renews it; a refusal below must not
    # then take away a lease the caller already had, with its tree full of work.
    prior, held_before = _prior_lease(st, cfg, item, log.agent_id)
    early = _early_refusal(
        repo, cfg, events, st, item, want, log.agent_id, force=force, held_before=held_before
    )
    if early is not None:
        return early
    lz = _acquire(repo, log, cfg, st, item, want, prior, note, force, resources)
    if isinstance(lz, O.Outcome):
        return lz

    _leave_line(repo, log.agent_id, item)
    bound = _bind_tree(repo, log, cfg, st, item, want, held_before, called_from, no_worktree)
    if isinstance(bound, O.Outcome):
        return bound
    wt = bound.wt
    _bring_local_files(repo, cfg, wt)
    log.append("item.started", item, {})
    # The first branch made is where the branching model starts to matter. An unmade
    # choice is defaulted here, on the record, and followed from now on.

    CHO.adopt_defaults(log, cfg, ["model", "integration"])
    target_item = st.items.get(item)
    port = _apply_port(repo, cfg, log, st, item, target_item, wt, bound.ours)
    return _claimed(cfg, st, item, lz, bound, port, target_item, called_from or repo)


def _claimed(cfg, st, item: str, lz, bound: _Bound, port, target_item, stands: Path) -> O.Outcome:
    """The success answer of a claim: the lease, the tree and what governs the item."""
    wt = bound.wt
    guidance = GI.for_work(
        cfg,
        st,
        lz.globs,
        target_item.tags if target_item is not None else (),
        budget=GI.door_budget(cfg),
        heading="## Guidance that governs this item",
    ).text
    return O.ok(
        "item.claimed",
        # Only when something governs the item: the key is absent otherwise, as it always was.
        **({"guidance": guidance} if guidance else {}),
        port=port,
        port_advice=PT.advice(port, item) if port else "",
        item=item,
        holder=lz.holder,
        worktree=str(wt.path) if wt else "",
        branch=wt.branch if wt else "",
        adopted=bound.was_adopted
        if bound.rebound
        else bool(wt and not wt.created and not bound.ours),
        rebound=bound.rebound,
        # Whether the caller already stands in the tree it was given: a rebound tree
        # is usually somewhere else, and the caller has to be told to go there.
        here=bool(wt and _tree_of(stands) == Path(wt.path).resolve()),
        ttl_s=cfg.lease.ttl_s,
        heartbeat_s=cfg.lease.heartbeat_s,
        base=wt.base if wt else "",
        # What the lease now covers, so the caller need not read the log to learn it
        # (Bb3cb64444e: ten --globs flags recorded none, and nothing said so).
        globs=list(lz.globs),
    )


def _acquire(repo: Path, log, cfg, st, item: str, want, prior, note, force, resources):
    """The lease for ``item``, or the refusal (an `Outcome`) naming who holds it."""
    try:
        return L.acquire(
            log,
            cfg,
            item,
            globs=want,
            note=note,
            force=force,
            resources=csv_list(resources) or None,
            offer=alternatives_offer(repo, log, cfg),
        )
    except L.LeaseError as exc:
        reason = str(exc)
        if exc.alternatives:
            reason += "\n\nYou could take instead: " + ", ".join(exc.alternatives)
        own = prior if prior is not None and prior.holder == exc.holder else None
        lapsed = own is not None and not own.live(time.time(), cfg.lease.grace_s)
        if exc.holder and exc.holder != log.agent_id and not lapsed:
            _join_line(
                repo,
                cfg,
                log.agent_id,
                item,
                _blocking_items(st, cfg, item, want, log.agent_id),
                str(exc),
            )
            # The refusal is the moment an agent decides between idling, polling and
            # asking a person. None of the three is needed when the block is a live
            # holder: `wait` wakes it when that holder lets go. Not for an EXPIRED lease,
            # which `wait` refuses at once -- that one is `recover`'s.
            reason += (
                f"\n\nOr `ddflow wait --item {item}`: it sleeps until {exc.holder} lets "
                f"go and returns the moment the item can be claimed."
            )
        return O.refused("item.claimed", reason, id=item, alternatives=list(exc.alternatives or []))


def _bind_tree(
    repo: Path, log, cfg, st, item: str, want, held_before: bool, called_from, no_worktree: bool
) -> _Bound | O.Outcome:
    """The tree this claim binds the item to, or the refusal that undid the claim."""
    if not cfg.worktree.enabled or no_worktree:
        return _Bound()
    recorded = _recorded_tree(repo, st.items.get(item))
    if recorded is not None:
        return _rebind(repo, log, cfg, st, item, want, recorded)
    adopted = W.current(called_from or repo) if cfg.worktree.adopt_existing else None
    if adopted is not None:
        return _adopt(repo, log, cfg, st, item, want, held_before, adopted)
    return _create_tree(repo, log, cfg, st, item, want, held_before)


def _rebind(repo: Path, log, cfg, st, item: str, want, recorded: Path) -> _Bound:
    # The item already has a tree, and it still exists: that tree -- and its branch,
    # with whatever unmerged work is on it -- is the item's, wherever the caller is
    # standing. Adopting the caller's tree instead made `merge` merge nothing.
    it = st.items[item]
    branch = it.branch
    stored = W.store_path(repo, recorded)
    wt = W.Worktree(item=item, path=recorded, branch=branch, base=it.base, created=False)
    L.acquire(log, cfg, item, worktree=stored, branch=branch, globs=want, force=True)
    return _Bound(wt, ours=not it.adopted, rebound=True, was_adopted=bool(it.adopted))


def _adopt(
    repo: Path, log, cfg, st, item: str, want, held_before: bool, adopted
) -> _Bound | O.Outcome:
    stored = W.store_path(repo, adopted.path)
    held = _worktree_held_by(st, stored, item, repo, cfg)
    if held:
        # RELEASE before refusing -- see the module docstring.
        _undo_claim(log, item, held_before, "claim refused: worktree conflict")
        return O.refused(
            "item.claimed",
            f"this worktree is already bound to {held}, which is still open. "
            f"Two items sharing one tree cannot be merged or recovered "
            f"separately. Finish {held}, work somewhere else, or "
            f"`--no-worktree` to claim without binding a tree.",
            id=item,
            conflicts_with=held,
        )
    wt = W.Worktree(item=item, path=adopted.path, branch=adopted.branch, base="", created=False)
    log.append("worktree.adopted", item, {"path": stored, "branch": wt.branch, "base": ""})
    L.acquire(log, cfg, item, worktree=stored, branch=wt.branch, globs=want, force=True)
    return _Bound(wt)


def _create_tree(
    repo: Path, log, cfg, st, item: str, want, held_before: bool
) -> _Bound | O.Outcome:
    try:
        base, branch = ("", "")
        if item in st.items:
            # The branching model decides the fork point: gitflow's develop or
            # production, or a dependency's unmerged branch when stacking.
            base, branch = FS.fork_point(repo, cfg, st, st.items[item])
        wt = W.create(repo, cfg, item, base=base, branch=branch)
        if not wt.created:
            # `W.create` reuses whatever tree sits at the default path. One on
            # ANOTHER branch is not this item's: binding it recorded a branch
            # that is not checked out there, and `merge` merged the wrong work.
            if not _is_items_tree(repo, wt.path, wt.branch):
                head = W.git(wt.path, "rev-parse", "--abbrev-ref", "HEAD")
                there = head.out if head.ok else "something else"
                there = "a detached HEAD" if there == "HEAD" else there
                _undo_claim(log, item, held_before, "claim refused: worktree path occupied")
                return O.refused(
                    "item.claimed",
                    f"{wt.path} already exists with {there} checked out, which "
                    f"does not carry {wt.branch}. It is not {item}'s tree; move or "
                    f"remove it, or claim from a tree of your own.",
                    id=item,
                    path=str(wt.path),
                )
        stored = W.store_path(repo, wt.path)
        log.append(
            "worktree.created",
            item,
            {"path": stored, "branch": wt.branch, "base": wt.base},
        )
        L.acquire(log, cfg, item, worktree=stored, branch=wt.branch, globs=want, force=True)
        return _Bound(wt, ours=True)
    except W.GitError as exc:
        return O.failed("item.claimed", f"lease held, but worktree creation failed: {exc}", id=item)


def _apply_port(repo: Path, cfg, log, st, item: str, target_item, wt, ours: bool) -> dict[str, Any]:
    """The port this claim applies (or tells the agent to), `{}` for an item that is none."""
    if not (
        target_item is not None
        and (target_item.port_from or target_item.promote_to)
        and not target_item.port
    ):
        return {}
    # Applied in a tree ddflow made -- also on a RE-claim, which is how a port claimed
    # with --force before its source landed gets applied once it has. In an adopted
    # tree, or with no tree, ddflow does not rewrite the agent's files: it says what
    # to do instead of staying silent about it being a port at all.
    return PT.apply(repo, cfg, log, st, item, wt.path) if ours else PT.manual(repo, cfg, st, item)


def _new_report_count(st, item: str) -> int:
    """Reports on `item` since its lease was taken (additions and records linked to it)."""

    it = st.items.get(item)
    if it is None or it.lease is None:
        return 0
    return new_reports(st, item, it.lease.acquired_at)["count"]


def _early_refusal(
    repo: Path, cfg, events, st, item: str, want, me: str, *, force: bool, held_before: bool
) -> O.Outcome | None:
    """A claim turned away before it asks for the lease: the item is looping, or its
    files are held for a waiter in line. (An `Outcome` is falsy when refused, so these
    are compared to None, never chained with `or`.)"""
    looped = _refuse_looping(events, st, cfg, item, force)
    if looped is not None:
        return looped
    return _refuse_if_reserved(repo, cfg, st, item, want, me, skip=force or held_before)


def _refuse_looping(events, st, cfg, item: str, force: bool) -> O.Outcome | None:
    """The refusal for an item that is already looping (`[loops].on_detect = "block"`)."""

    looping = [f for f in PR.detect(events, st, cfg) if f.item == item and f.severity == "block"]
    if not looping or force:
        return None
    return O.refused(
        "item.claimed",
        f"refusing to claim {item}: it is already looping.\n"
        + "\n".join(f"  {f.render()}" for f in looping)
        + "\n\nRe-claiming it would continue the loop. Change the task, abandon it, "
        "or --force if you have fixed the underlying cause.",
        id=item,
        looping=[f.__dict__ for f in looping],
    )


def _prior_lease(st, cfg, item: str, me: str):
    """(the item's lease as folded, whether it is a LIVE one of ``me``'s). A holder
    re-claiming its own live lease only renews it."""
    it = st.items.get(item)
    prior = it.lease if it is not None else None
    live = bool(prior and prior.holder == me and prior.live(time.time(), cfg.lease.grace_s))
    return prior, live


def _refuse_if_reserved(
    repo: Path, cfg, st, item: str, want, me: str, *, skip: bool
) -> O.Outcome | None:
    """The refusal for a claim of files that are held for a waiter in line; None if free."""
    it = st.items.get(item)
    if skip or it is None:
        return None
    now = time.time()
    live = st.active_leases(now, cfg.lease.grace_s)
    ahead = _reserved_for(
        repo, st, cfg, it, me, list(want if want is not None else it.globs), live, now
    )
    if ahead is None:
        return None
    reason = _reserved_msg(item, ahead, cfg)
    _join_line(repo, cfg, me, item, [ahead.item], reason)
    return O.refused(
        "item.claimed",
        reason + f"\n\n`ddflow wait --item {item}` sleeps until it is yours; your place in "
        f"line is kept while you wait or keep asking.",
        id=item,
        alternatives=[],
        reserved_for=ahead.agent,
    )


def _blocking_items(st, cfg, item: str, want, me: str) -> list[str]:
    """The held items a refused claim of ``item`` is waiting behind: what `waiters` names."""
    it = st.items.get(item)
    if it is None:
        return [item]
    now = time.time()
    clash = L.glob_clash(st, cfg, it, me, list(want if want is not None else it.globs), now)
    return [clash[0]] if clash else [item]


def _join_line(repo: Path, cfg, agent: str, item: str, waiting_on: list[str], why: str) -> None:
    """A refused claim is a place in line (`waits.queue`). Never fails the refusal."""

    if cfg.lease.waiter_reservation_s <= 0:
        return
    try:
        WT.queue(
            repo,
            agent,
            item,
            waiting_on=waiting_on,
            reason=why,
            window_s=cfg.lease.waiter_reservation_s,
        )
    except (OSError, ValueError, TypeError):
        pass  # advisory: no queue place is better than no refusal


def _leave_line(repo: Path, agent: str, item: str) -> None:
    """The claim landed: the place in line it held is spent."""

    try:
        WT.clear(repo, agent, item)
    except (OSError, ValueError, TypeError):
        pass  # advisory
