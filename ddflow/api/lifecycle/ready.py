"""`next_` (`ddflow next`): what may start now, and why everything else may not.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core import progress as PR
from ...core.model import REVIEW
from ...core.plain import plain
from .._base import _load
from .reservations import WAITABLE, _reservation_hold

#: What `next` offers when nobody says otherwise. TASKS, because a phase is an umbrella
#: and "work on P1" is not an instruction anyone can act on.
#:
#: Defaulted HERE as well as in argparse, and that duplication is the point: this function
#: was first written with `kind=""`, which `plan()` matches against no item at all, so
#: `ddflow_next` returned an empty queue on every call. The CLI kept working because
#: argparse supplied "task" and the MCP path no longer went through argparse. A default
#: that lives only in the parser is a default the typed layer silently drops.
DEFAULT_NEXT_KIND = "task"

#: `brief` scans for recoverable work unless told not to. On by default because the one
#: moment an agent most needs to know a previous agent crashed mid-task is the moment it
#: is about to start work.
DEFAULT_CHECK_RECOVERY = True


def next_(
    repo: Path, *, kind: str = DEFAULT_NEXT_KIND, phase: str = "", agent: str = ""
) -> O.Outcome:
    """Offer the next actionable item(s). Exit 2 when nothing is actionable, 1 when
    ``phase`` names no item (`_unknown_phase`)."""
    from ...core.schedule import critical_path, plan

    log, cfg, st = _load(repo, agent)
    unknown = _unknown_phase(st, phase)
    if unknown:
        return O.failed("next", unknown, phase=phase)
    promoted: list[str] = []
    if cfg.flow.auto_promote:
        # Continuous delivery where the operator asked for it: an environment in
        # auto_promote whose upstream moved gets its promotion filed here, and offered
        # below like any other task.
        from ...services import promotions as PM

        promoted = PM.auto(repo, cfg, log, st)
        if promoted:
            log, cfg, st = _load(repo, agent)
    synced: dict[str, Any] = {}
    if (
        cfg.flow.integration == "pr"
        and cfg.flow.sync_on_next
        and (
            any(i.state == REVIEW for i in st.items.values())
            # A hotfix's back-merge request outlives its item (B171): the item completes
            # the moment it is opened, so "something in review" alone never re-asks.
            or any(r["state"] == "open" for r in st.back_merges.values())
        )
    ):
        # Reviewers act between an agent's turns. Asking here is what lets a merged
        # request complete, and a requested change come back as work, without anyone
        # remembering to run `pr sync` -- the loop stays `next`, `claim`, work, `merge`.
        from ...services import flow as FS

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
    from ...services.flowstate import limit_for

    p = plan(
        st,
        cfg,
        kind=kind,
        phase=phase,
        agent=me,
        hold=_reservation_hold(repo, st, cfg, me),
        parallel=limit_for(repo, cfg, st, log.read_all),
    )
    data: dict[str, Any] = {
        "review": [i.id for i in p.review],
        "synced": synced,
        "promoted": promoted,
        "ready": _ready_rows(p.ready),
        "blocked": [plain(b) for b in p.blocked],
        "running": [i.id for i in p.running],
        "cycles": p.cycles,
        "interrupted": p.interrupted,
        "critical_path": critical_path(st, phase),
        "finished_phases": p.finished,
        "_render": {"plan": p},
    }
    if p.ready:
        return O.ok("next", **data)
    # Still exit 2 with only a phase to close: "no task left" is the driver's cue for the
    # phase close, which `close_note` then spells out (B28268eba1a).
    head = "Nothing to claim" if p.finished else "Nothing actionable"
    return O.nothing("next", f"{head} ({p.summary()}).{_wait_hint(p)}{p.close_note()}", **data)


def _ready_rows(items) -> list[dict[str, Any]]:
    """The ready items as rows; a `tier:` tag also as a `tier` field (advisory, so an
    untagged item carries nothing extra)."""
    from ...core.tier import tier_of

    rows = []
    for i in items:
        row = plain(i)
        if tier := tier_of(i.tags):
            row["tier"] = tier
        rows.append(row)
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
