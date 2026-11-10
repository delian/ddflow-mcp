"""The shared view of live leases every claim, renew and retarget asks for.

``_fold`` reads the log into a ``State`` outside the append lock; the clash predicates
decide which overlapping globs another lease really covers.
"""

from __future__ import annotations

from pathlib import Path

from ...config import Config
from ...core import clock
from ...core import globs as GL
from ...core.admission import conflicts, glob_conflict, real_overlap
from ...core.admission import glob_clashes as _glob_clashes
from ...core.flow import branch_name, line_key
from ...core.model import Item, Lease, State, fold
from ...infra import worktree as W
from ...infra.log import EventLog, bare_agent_id
from .. import changes as CH
from .. import leases as _leases
from .. import probe as PR
from ..leaseclock import localize


def _clone_field(log: EventLog, holder: str) -> dict[str, str]:
    """The clone a claim is made from, for the event payload; nothing when it has none
    (an older reader ignores the field: D-compat additive). A claim under the bare
    derived id (from before the clone suffix) only READS the clone id, never creates it:
    the suffix's creation time is what marks that id's claims as pre-suffix."""
    clone = log.clone_id(create=holder != bare_agent_id(log.root))
    return {"clone": clone} if clone else {}


def _fold(log: EventLog) -> State:
    """The state of the log as read now: the `decide` that `EventLog.decide_then_append`
    runs outside the append lock, and again under it if the log changed in between."""
    return localize(fold(log.read_all(), strict=False), log, _leases.time.time())


def glob_clashes(
    state: State, cfg: Config, it: Item, holder: str, globs: list[str], now: float
) -> list[tuple[str, Lease, tuple[str, str]]]:
    """Every OTHER live lease ``globs`` would overlap: [(item id, lease, pair), ...].

    `glob_clash` is the first of these; `claim` and `update --globs` name them all.
    """
    return [
        (c.item, c.lease, c.pair)
        for c in _glob_clashes(
            state,
            cfg,
            it,
            globs,
            holder,
            against="live",
            live=unfinished_leases(state, cfg, now),
        )
    ]


def clash_report(clashes: list[tuple[str, Lease, tuple[str, str]]]) -> tuple[str, list[str]]:
    """The clash list as (text, item ids): one `'mine' overlaps 'theirs' held by H on I`
    clause per clash, and every conflicting item id (a waiter must wait on each), so one
    refusal names them all instead of the first (B-glob-refusal-names-one-conflict)."""
    text = "; ".join(
        f"{p0!r} overlaps {p1!r} held by {lz.holder} on {oid}" for oid, lz, (p0, p1) in clashes
    )
    # One other item that overlaps on two globs yields two clashes; a waiter must wait on
    # that item once, not twice.
    return text, list(dict.fromkeys(oid for oid, _lz, _p in clashes))


def glob_clash(
    state: State, cfg: Config, it: Item, holder: str, globs: list[str], now: float
) -> tuple[str, Lease, tuple[str, str]] | None:
    """The first OTHER live lease ``globs`` would overlap: (item id, lease, pair), or None.

    One copy, asked by `claim` and by `update --globs` on a claimed item, so widening a
    claim cannot take paths that claiming them would have been refused.
    """
    clash = glob_conflict(
        state,
        cfg,
        it,
        globs,
        holder,
        against="live",
        live=unfinished_leases(state, cfg, now),
    )
    return (clash.item, clash.lease, clash.pair) if clash else None


def unfinished_leases(state: State, cfg: Config, now: float) -> dict[str, Lease]:
    """The live leases of items still in progress. A finished item holds nothing: its
    lease may be unreleased, but its files are free."""
    return {
        i: lz
        for i, lz in state.active_leases(now, cfg.lease.grace_s).items()
        if not (i in state.items and state.items[i].terminal)
    }


def _holder_ref(repo: Path, lz: Lease, other: Item | None, cfg: Config, take: float) -> str:
    """The holder's side of the probe: its branch when git can resolve it to a commit, the
    default branch when the branch is unborn (a fresh holder forks from there, like a fresh
    claimant), or "" for a name git could not answer (a timeout, a missing git) -- a no-answer
    must stay exclusive, never fall back to the default branch in its place."""
    name = lz.branch if lz.branch else (branch_name(other, cfg) if other else "")
    if not name:
        return ""
    _sha, why = PR.tip(repo, name, take)
    if not _sha:
        return W.default_branch(repo) if "not a commit of this repository" in why else ""
    return name


def optimistic_clashes(
    log: EventLog,
    cfg: Config,
    state: State,
    it: Item,
    holder: str,
    clashes: list[tuple[str, Lease, tuple[str, str]]],
) -> tuple[list[tuple[str, Lease, tuple[str, str]]], list[str]]:
    """Split clashing pairs into what stays exclusive and what a clean merge-tree probe
    shares optimistically (DX-lease-hunk-optimistic).

    Off (default) every clash stays exclusive (`D-lease-overlap-only-by-break`). On, a
    pair whose holder's branch merges cleanly into the claimant's branch is dropped as
    optimistic -- git auto-merges the two branches' disjoint hunks, and the merge rebases
    the second instead of waiting -- and its glob is returned for the claim to record. A
    conflict, an unknown holder branch, or a probe that could not run stays exclusive: a
    silent no-answer fails closed to the refusal, never to an optimistic grant.
    """
    if not cfg.locks.optimistic:
        return clashes, []
    # The claimant's side of the probe is its ACTUAL branch -- the changes it would merge
    # with the holder's (the task's "the two branches' changes") -- and the default branch
    # only while it has none yet: a fresh claim forks from there, so the holder need only
    # merge clean into it. An unborn branch name resolves to the default branch.
    take = cfg.locks.probe_timeout_s
    mine_ref = it.lease.branch if it.lease and it.lease.branch else branch_name(it, cfg)
    if not mine_ref:
        # No branch name at all: a fresh claim forks from the default branch, so the
        # holder need only merge clean into it.
        mine_ref = W.default_branch(log.root)
    else:
        _sha, _why = PR.tip(log.root, mine_ref, take)
        if not _sha and "not a commit of this repository" in _why:
            # Unborn: the branch names nothing yet -- a fresh claim forking from the
            # default branch -- so the holder need only merge clean into it there.
            mine_ref = W.default_branch(log.root)
    kept: list[tuple[str, Lease, tuple[str, str]]] = []
    optimistic: list[str] = []
    seen: dict[tuple[str, str], PR.Probe] = {}
    for other_id, lz, (mine, theirs) in clashes:
        other = state.items.get(other_id)
        holder_ref = _holder_ref(log.root, lz, other, cfg, take)
        if not holder_ref:
            # A holder branch git could not resolve to a commit (a timeout, a missing git) is
            # a no-answer against the holder's real tip: stay exclusive (see `mine_ref`).
            kept.append((other_id, lz, (mine, theirs)))
            continue
        probe = seen.get((mine_ref, holder_ref))
        if probe is None:
            probe = PR.run(
                log.root,
                mine_ref,
                holder_ref,
                timeout_s=cfg.locks.probe_timeout_s,
                min_git=cfg.locks.probe_merge_tree_min_git,
            )
            seen[(mine_ref, holder_ref)] = probe
        if probe.clean:
            optimistic.append(mine)
        else:
            kept.append((other_id, lz, (mine, theirs)))
    # A glob granted optimistic for one holder is NOT optimistic if another holder's pair
    # still clashes on it -- overlapped, not merely the same string, so a broad `src/**`
    # clean against one holder and a real conflict on `src/a.py` against another never half-
    # shares the file.
    kept_mine = [m for _o, _lz, (m, _t) in kept]
    optimistic = [
        g for g in dict.fromkeys(optimistic) if not any(GL.overlap(g, m) for m in kept_mine)
    ]
    return kept, optimistic


def holder_changed_paths(log: EventLog, it: Item) -> list[str] | None:
    """The paths the lease holder's item ``it`` has actually changed, or None when git
    cannot say (no worktree, unreadable): unknown never means "disjoint". Run only for a
    holder whose globs already clash, so the git reads cost a refusal, never a claim."""
    try:
        path = W.load_path(log.root, it.worktree) if it.worktree else None
        if path is None or not path.is_dir():
            return None
        return CH.changed_paths(path, it.base or W.default_branch(log.root))
    except Exception:
        return None


def _clash_text(
    log: EventLog, cfg: Config, state: State, it: Item, globs: list[str], now: float
) -> tuple[str, str, list[str]] | None:
    """(holder item, text naming it, globs left after dropping the disjoint ones), or None
    when nothing overlaps once the holder's real changes are compared.

    A directory glob held by another lease covers files its holder never touched; the
    holder's changed paths say which. Each requested glob that overlaps a live lease is
    kept as a clash only when it overlaps one of those changed paths (or they are unknown).
    """
    shared = _leases.shared_globs(cfg)
    mine = it.lease.holder if it.lease else ""
    line = line_key(state, it, cfg)
    first, parts, bad = "", [], set()
    for other_id, lz in unfinished_leases(state, cfg, now).items():
        other = state.items.get(other_id)
        if other_id == it.id or other is None or lz.holder == mine:
            continue  # itself or my own
        if line_key(state, other, cfg) != line:
            continue  # another release line is another branch: no clash
        pairs = _live_pairs(log, other, lz, globs, shared)
        if not pairs:
            continue
        listed = "; ".join(f"{a!r} overlaps {b!r}" for a, b in pairs)
        parts.append(f"held by {lz.holder} on {other_id} for {_age(now, lz)}: {listed}")
        first = first or other_id
        bad |= {a for a, _ in pairs}
    if not first:
        return None
    return first, " | ".join(parts), [g for g in globs if g not in bad]


def _age(now: float, lz: Lease) -> str:
    held = max(now - lz.acquired_at, 0)
    return (
        clock.fmt_age(held, "h", places=1)
        if held >= clock.AGE_UNITS["h"][0]
        else clock.fmt_age(held, "min")
    )


def _live_pairs(
    log: EventLog, other: Item, lz: Lease, globs: list[str], shared: list[str]
) -> list[tuple[str, str]]:
    """The (mine, theirs) glob pairs that really clash with ``lz``, once the files its
    holder has changed are compared (`AD.real_overlap`, DX-lease-real-overlap): a WILDCARD
    lease glob can cover files its holder never touches; an exact path it claimed is
    declared work, changed yet or not. The git read costs a refusal, never a claim."""
    pairs = conflicts(globs, lz.globs, shared)
    if not pairs:
        return pairs
    changed = _leases.holder_changed_paths(log, other)
    return real_overlap(globs, lz.globs, changed, shared)
