"""`next_` (`ddflow next`): what may start now, and why everything else may not.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import difflib
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core import progress as PR
from ...core.defaults import DEFAULT_NEXT_KIND
from ...core.model import REVIEW
from ...core.plain import plain
from ...core.schedule import (
    Blocked,
    bug_items,
    co_location_hints,
    critical_path,
    glob_conflict,
    wave_of,
    with_tags,
)
from ...core.tier import tier_of
from ...services import flow as FS
from ...services import leases as LS
from ...services import promotions as PM
from .._base import _load
from .planning import plan_for_offer
from .reservations import WAITABLE

#: `brief` scans for recoverable work unless told not to. On by default because the one
#: moment an agent most needs to know a previous agent crashed mid-task is the moment it
#: is about to start work.
DEFAULT_CHECK_RECOVERY = True


def next_(
    repo: Path,
    *,
    kind: str = DEFAULT_NEXT_KIND,
    phase: str = "",
    agent: str = "",
    phase_only: bool = False,
    flat: bool = False,
    exclude_leased: bool = False,
    tags: Sequence[str] = (),
    ignore_cap: bool = False,
) -> O.Outcome:
    """Offer the next actionable item(s). Exit 2 when nothing is actionable, 1 when
    ``phase`` names no item (`_unknown_phase`).

    ``flat`` (False) groups the ready list by ``wave:N`` bucket (bugs and
    dependency-carrying items lead each bucket); ``exclude_leased`` drops ready items
    whose globs overlap another agent's live lease to ``excluded_leased``. ``ignore_cap`` offers
    the items only the parallelism cap holds back as ready (a named claim is not capped).
    ``tags`` (every
    one required, ``wave:N`` included) limits the offer to items carrying them; a tag no
    item carries is a refusal naming the nearest ones, never an empty answer.
    """

    log, cfg, st = _load(repo, agent)
    unknown = _unknown_phase(st, phase)
    if unknown:
        return O.failed("next", unknown, phase=phase)
    tags = _clean_tags(tags)
    if unknown_tag := _unknown_tag(st, tags):
        return O.failed("next", unknown_tag, tags=list(tags))
    promoted: list[str] = []
    if cfg.flow.auto_promote:
        # Continuous delivery where the operator asked for it: an environment in
        # auto_promote whose upstream moved gets its promotion filed here, and offered
        # below like any other task.

        promoted = PM.auto(repo, cfg, log, st)
        if promoted:
            log, cfg, st = _load(repo, agent)
    synced: dict[str, Any] = {}
    if _wants_pr_sync(cfg, st):
        # Reviewers act between an agent's turns. Asking here is what lets a merged
        # request complete, and a requested change come back as work, without anyone
        # remembering to run `pr sync` -- the loop stays `next`, `claim`, work, `merge`.

        rep = FS.sync(repo, cfg, log)
        synced = {
            "changes": [f"{c.item}: {c.what}" for c in rep.changes],
            "unavailable": rep.unavailable,
            # A closed-unmerged back-merge (or a merge the forge refused) is said ONCE:
            # it is recorded as settled, so a `next` that dropped it never told anyone.
            "refused": rep.refused,
        }
        if rep.changes:
            log, cfg, st = _load(repo, agent)
    me = cfg.agent.id or log.agent_id
    p = plan_for_offer(
        repo, log, cfg, st, kind=kind, phase=phase, agent=me, phase_only=phase_only, tags=tags
    )
    p.blocking_deps = with_tags(p.blocking_deps, tags)
    held = _take_cap_held(p, st, ignore_cap)
    excluded = _offer_ready(p, st, cfg, me, flat=flat, exclude_leased=exclude_leased)
    if exclude_leased and held:
        # A cap-held item whose files another agent holds is not claimable either.
        held, more = _drop_leased_overlap(held, st, cfg, me)
        excluded += more
    LS.surface_contests(p, st, cfg)  # B2809f4213b: a contested claim is not 'running'
    data: dict[str, Any] = {
        "review": [i.id for i in p.review],
        "synced": synced,
        "promoted": promoted,
        "ready": _ready_rows(p.ready),
        "held_by_cap": _ready_rows(held),
        "needs_complete": [i.id for i in _merged_uncompleted(p, st)],
        "co_location_hints": co_location_hints(p.ready),
        "excluded_leased": [plain(b) for b in excluded],
        "blocking_deps": _blocking_dep_rows(p.blocking_deps),
        "blocked": [plain(b) for b in p.blocked],
        "running": [i.id for i in p.running],
        "cycles": p.cycles,
        "interrupted": p.interrupted,
        "critical_path": critical_path(st, phase, cfg),
        "finished_phases": p.finished,
        "filter": {"tag": list(tags), "phase": phase},
        "_render": {"plan": p},
    }
    if contested := [LS.contest_row(st, cfg, i) for i in LS.contested_items(st)]:
        data["contested"] = contested
    if _actionable(p):
        return O.ok("next", **data)
    # Still exit 2 with only a phase to close: "no task left" is the driver's cue for the
    # phase close, which `close_note` then spells out (B28268eba1a).
    head = "Nothing to claim" if p.finished else "Nothing actionable"
    if held:
        # Not 'nothing actionable': unblocked work exists, only the cap withholds it.
        head = "No free slot under the cap"
    return O.nothing(
        "next", f"{head} ({p.summary()}).{_held_hint(held)}{_wait_hint(p)}{p.close_note()}", **data
    )


def _clean_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """``tags`` stripped of blanks and repeats, in the order given."""
    return tuple(dict.fromkeys(t.strip() for t in tags if t and t.strip()))


def _unknown_tag(st, tags: Sequence[str]) -> str:
    """Why ``tags`` cannot slice the queue: one that NO live item carries, with the nearest
    tags that exist ("" when every tag is in use). An empty slice of a typo reads as 'nothing
    to do', and a driver takes that for a finished wave."""
    known = {t for i in st.items.values() if not i.removed for t in i.tags}
    for tag in tags:
        if tag not in known:
            near = difflib.get_close_matches(tag, sorted(known), n=5, cutoff=0.6)
            hint = f" Nearest: {', '.join(near)}." if near else ""
            return f"no item carries the tag {tag!r}.{hint}"
    return ""


def _take_cap_held(p, st, ignore_cap: bool) -> list:
    """The items ONLY the parallelism cap (or a reserved slot) keeps out of ``ready``.

    By default they stay in ``p.blocked`` too (the key's rows are unchanged) and are
    returned for the ``held_by_cap`` section. With ``ignore_cap`` they join ``p.ready`` and
    leave ``blocked``, and nothing is returned: a named `claim` is never capped (B30f9d383ba),
    so they are claimable now."""
    held = [st.items[i] for i in p.capped if i in st.items]
    if not (ignore_cap and held):
        return held
    ids = {i.id for i in held}
    p.ready = [*p.ready, *held]
    p.blocked = [b for b in p.blocked if b.item not in ids]
    p.capped = []
    return []


def _merged_uncompleted(p, st) -> list:
    """The blocked items that are merged but not completed: they need `ddflow complete`, not
    work, so they get their own section instead of crowding the blocked list."""
    return [st.items[b.item] for b in p.blocked if st.items[b.item].merged_sha]


def _held_hint(held: list) -> str:
    """The sentence that keeps 'Nothing actionable' from reading as 'nothing unblocked'."""
    if not held:
        return ""
    return (
        f" {len(held)} item(s) are ready and held ONLY by the cap (listed below, or "
        f"`--ignore-cap`): a named `ddflow claim` of one is not capped and grants it now."
    )


def _actionable(p) -> bool:
    """Is there something for a caller to START right now: an in-phase ready item, or a
    ready upstream blocking-dependency (offered to a lane asked to drive that phase)."""
    return bool(p.ready or p.blocking_deps)


def _wants_pr_sync(cfg, st) -> bool:
    """Should `next` sync the forge first: pr integration with sync-on-next, and an item in
    review (or a hotfix back-merge open) whose settle re-asks here."""
    return (
        cfg.flow.integration == "pr"
        and cfg.flow.sync_on_next
        and (
            any(i.state == REVIEW for i in st.items.values())
            or any(r["state"] == "open" for r in st.back_merges.values())
        )
    )


def _offer_ready(p, st, cfg, agent, *, flat: bool, exclude_leased: bool) -> list[Blocked]:
    """Shape the plan's offers for the caller: drop leased-overlap items (``exclude_leased``)
    and group the rest by wave (``flat`` keeps the flat optimizer order). Returns the
    dropped items, so `next_` reports them distinctly from ``blocked``."""
    dropped: list[Blocked] = []
    if exclude_leased:
        p.ready, dropped = _drop_leased_overlap(p.ready, st, cfg, agent)
    if not flat:
        p.ready.sort(key=_group_key(bug_items(st, cfg)))
    return dropped


def _drop_leased_overlap(items, st, cfg, agent):
    """Drop from ``items`` any item whose globs overlap a live lease held by ANOTHER agent,
    returning ``(kept, dropped)`` -- ``dropped`` as `Blocked` records the caller reports.

    Under ``[schedule] ready_policy = deps_and_lease`` (the default) the ``globs`` filter
    has already moved these to ``blocked``, so this is a no-op there; it exists so an
    orchestrator can ask `next_item(..., exclude_leased=True)` for the contention-free view
    WITHOUT changing the project's policy. Kept live here, not in the plan, because
    `Plan` holds only its offers -- a lease the caller must judge is a caller concern.
    """
    live = st.active_leases(time.time(), cfg.lease.grace_s)
    kept: list = []
    dropped: list[Blocked] = []
    for it in items:
        clash = glob_conflict(st, cfg, it, list(it.globs), agent, against="live", live=live)
        if clash is None:
            kept.append(it)
            continue
        lease = clash.lease
        holder = lease.holder if lease is not None else "?"
        dropped.append(
            Blocked(
                it.id,
                "conflict",
                f"globs overlap {clash.item} leased by {holder} "
                f"({clash.pair[0]} vs {clash.pair[1]})",
                [clash.item],
            )
        )
    return kept, dropped


def _group_key(bugs: set[str]):
    """The default ready ordering: group by ``wave:N`` bucket (unstamped last), and within
    a bucket lead bugs, then dependency-carrying items, then the rest. The sort is STABLE
    over the configured optimizer order, so the ready set is grouped, not re-prioritised
    inside a bucket. ``--flat`` skips this and leaves the flat optimizer order."""

    def key(it) -> tuple:
        w = wave_of(it.tags)
        rank = 0 if it.id in bugs else (1 if it.needs else 2)
        return (w is None, w if w is not None else -1, rank)

    return key


def _ready_rows(items) -> list[dict[str, Any]]:
    """The ready items as rows; a `tier:` tag also as a `tier` field (advisory, so an
    untagged item carries nothing extra), and the ``wave:N`` tag as a `wave` field (None
    when unstamped), so an orchestrator can group by wave from `--json` too."""

    rows = []
    for i in items:
        row = plain(i)
        row["wave"] = wave_of(i.tags)
        if tier := tier_of(i.tags):
            row["tier"] = tier
        rows.append(row)
    return rows


def _blocking_dep_rows(items) -> list[dict[str, Any]]:
    """A blocking dependency's ready item as a row, in the ready-row shape plus an explicit
    `blocking_dependency` marker -- the label that keeps it distinct from an in-phase item."""
    rows = _ready_rows(items)
    for row in rows:
        row["blocking_dependency"] = True
    return rows


#: How many ids under an unknown `--phase` prefix the refusal names before "and N more".
_PREFIX_SHOWN = 12


def _unknown_phase(st, phase: str, *, phases_only: bool = False) -> str:
    """Why ``phase`` names nothing ``next``, ``brief`` or ``board`` can slice by; "" when
    a slice is possible: any live item for ``next`` and ``brief``, and with
    ``phases_only`` (``board``, which slices by phase id) only a phase.

    An empty slice of an id that is not an item read as "Nothing actionable", exit 2, and
    a driver took that for "phase done" (Bde0c6e9fad: `--phase 159`, whose work lived
    under 159.A..159.I). The ids that start with it are named: they are what was meant.
    """
    if not phase:
        return ""
    it = st.items.get(phase)
    if it is not None and not it.removed:
        if not phases_only or it.kind == "phase":
            return ""
        # `board` slices by PHASE id: a task id would answer an empty board at exit 0.
        owner = PR.phase_of(st, it.id)
        where = f" -- it is under phase {owner!r}" if owner else ""
        return f"{phase!r} is a {it.kind}, not a phase{where}."
    under = sorted(
        i.id
        for i in st.items.values()
        if not i.removed and i.id.startswith(f"{phase}.") and "." not in i.id[len(phase) + 1 :]
    )
    gone = " (it was removed from the queue)" if it is not None else ""
    more = f" and {len(under) - _PREFIX_SHOWN} more" if len(under) > _PREFIX_SHOWN else ""
    hint = f" Items under that prefix: {', '.join(under[:_PREFIX_SHOWN])}{more}." if under else ""
    return f"no such phase or item {phase!r}{gone}.{hint}"


def _wait_hint(p) -> str:
    """Point a blocked caller at `wait` -- only when something in flight can clear it."""
    if p.running and any(b.reason in WAITABLE for b in p.blocked):
        return (
            " `ddflow wait` sleeps until one of the blocked items frees, and returns "
            "the moment it does — no need to poll or to ask a person."
        )
    return ""
