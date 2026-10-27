"""Mechanical lint over the WHOLE OPEN QUEUE -- `plan check` lifted from a proposed
document to the queue as it stands (DX-queue-lint).

Same purity contract as `plancheck`: it reads the SNAPSHOT it is given and does no I/O;
``band`` (the priority-banding classification with its number) and ``similar`` (the
queue's add-time duplicate hunt) are passed IN, so tests need no repository, no log and
no clock.

A snapshot is ``{id: item}`` over EVERY item the queue knows -- open and settled -- as
plain dicts with ``id``, ``kind``, ``state``, ``title``, ``body``, ``needs``, ``globs``,
``tags``, ``priority`` and ``terminal`` (done or abandoned). Settled items are IN the
snapshot because reachability walks THROUGH them (A needs T1 is an ordering edge even
when T1 is done); findings are filed only for ids in ``scope``, which is how
``--since``/``--phase`` narrow a run.

Findings, each naming its remedy:

* ``overlap`` -- two scoped work items neither reaches the other through `needs`, whose
  globs overlap: the same comparison `claim` refuses a lease for, so this reads the queue
  rather than guessing at it. A PROPOSED edge is added only where a rule picks its
  direction (the two below), checked against the queue's own graph: reachability is
  re-walked, and `autoorder.eligible` refuses a settled item, a decide task
  (`L-needs-on-decide-tasks`: never need a settled decide task), a `no-auto-order` tag or
  a non-task at either end.
* ``no_globs`` -- a scoped task with no declared globs: it collides with everything, and
  no ordering edge orders THAT. No auto-fix exists for a name nobody typed.
* ``stale_needs`` -- a `needs` naming an item that is settled or gone: the edge is
  already satisfied and reading it says nothing (`L-needs-on-decide-tasks`). The fix is
  to DROP the edge -- high-confidence.
* ``stale_ref`` -- an item's body NAMING a settled item's id: evidence pointing at work
  that is over.
* ``dx_priority`` -- a DX or bug-fix task at the generic default priority, behind every
  feature (`DX-default-priority-for-dx`). The fix is its band -- high-confidence.
* ``body_ref`` -- an item's body naming another OPEN item's id with no ordering edge
  between them: a reference nothing enforces, which may be the missing edge.
* ``duplicate`` -- a pair the add-time duplicate hunt would ask about.

Edge direction, in order:

1. exactly one side is a fix/DX item (``band`` says so): the OTHER side waits -- a fix
   never waits on a feature ('never add a needs edge from a fix/DX task to a feature
   task');
2. both fix/DX or both feature: the HIGHER priority number waits for the lower (lower
   runs first); equal priorities propose nothing -- priority is not a dependency intent.

Which fixes are HIGH-CONFIDENCE (what ``--fix`` applies): a ``stale_needs`` removal, a
``dx_priority`` band, and an ``overlap`` edge rule 1 forced (a fix/DX side involved). A
priority-ordered feature pair is reported with its proposed direction but NOT applied: a
number nobody typed as a dependency would otherwise serialize the queue, and
``--priority`` is the operator's act. ``body_ref``, ``stale_ref``, ``no_globs`` and
``duplicate`` are report-only: consolidating items and naming globs are the operator's
call.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core import autoorder as AO
from ..core.defaults import DEFAULT_PRIORITY
from ..core.globs import overlap as globs_overlap
from ..core.graph import closure

#: An id-shaped token in a body: what a mention of `RH-prune-needs` or `B-gap-plan-lint`
#: looks like. Every candidate is filtered against the snapshot's own ids, so a plain
#: word matches only when an item is literally named after it.
_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _needs(item: Mapping[str, Any]) -> list[str]:
    return [str(n) for n in item.get("needs", [])]


@dataclass(frozen=True)
class Finding:
    """One thing to fix on the queue. ``ids`` are item ids; ``fix`` is the high-confidence
    change only (see the module docstring), else None."""

    kind: str  #: overlap | no_globs | stale_needs | stale_ref | dx_priority | body_ref | duplicate
    ids: tuple[str, ...]
    detail: str
    #: {"needs_add": [later, earlier]} | {"needs_remove": [id]} | {"priority": n}
    fix: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ids": list(self.ids), "detail": self.detail, "fix": self.fix}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    #: How many ids were linted (``scope``), for the headline.
    items: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {"findings": [f.as_dict() for f in self.findings], "items": self.items}


def _reach_sets(items: Mapping[str, Any]) -> dict[str, set[str]]:
    """Every id reachable through the snapshot's `needs`, WALKED ONCE per id: the ordering
    test below asks this O(n) times per pair, and re-walking the queue's (deep) chains for
    each would cube the run. Edges leaving the snapshot are ignored (`closure`'s rule)."""
    nodes = set(items)
    edges = {n: [d for d in _needs(items[n]) if d in nodes] for n in nodes}
    return {n: set(closure(n, lambda x: edges[x])) for n in nodes}


def reaches(reach: Mapping[str, set[str]], start: str, target: str) -> bool:
    """Does ``start`` wait (transitively) on ``target``? True even when ``target`` is settled."""
    return target in reach.get(start, ())


def _cand(items: Mapping[str, Any], iid: str) -> AO.Cand | None:
    it = items.get(iid)
    if it is None:
        return None
    return AO.Cand(
        id=iid,
        terminal=bool(it.get("terminal")),
        kind=str(it.get("kind", "task")),
        tags=tuple(str(t) for t in it.get("tags", ())),
        needs=tuple(_needs(it)),
    )


FIXABLE_KINDS = frozenset({"dx", "bug"})


def propose(items: Mapping[str, Any], band: Callable[[str, tuple[str, ...]], str], a: str, b: str):
    """``(later, earlier)`` for the pair's ordering edge, or None (with a reason sentence).

    Direction per the module docstring's two rules; BOTH ends must pass the autoorder
    ordering guard, so a proposed edge never needs a settled decide task.
    """
    ka = band(a, tuple(str(t) for t in items[a].get("tags", ())))
    kb = band(b, tuple(str(t) for t in items[b].get("tags", ())))
    fa, fb = ka in FIXABLE_KINDS, kb in FIXABLE_KINDS
    if fa != fb:  # rule 1: the feature waits; the fix/DX side runs first
        later, earlier = (b, a) if fa else (a, b)
        forced = True
    else:  # rule 2: the higher priority number waits for the lower
        pa, pb = int(items[a]["priority"]), int(items[b]["priority"])
        if pa == pb:
            return None, f"{a} and {b} carry the same priority: which waits is the operator's call"
        later, earlier = (a, b) if pa > pb else (b, a)
        forced = False
    bad = AO.eligible(_cand(items, later)) or AO.eligible(_cand(items, earlier))
    if bad:
        return None, f"no legal direction: {bad}"
    return (later, earlier), "forced" if forced else "priority"


def overlaps(
    items: Mapping[str, Any],
    scope: Iterable[str],
    band: Callable[[str, tuple[str, ...]], str],
) -> list[Finding]:
    """Pairs that can run together AND would edit one file, with the proposed edge a rule
    can decide; nothing is proposed where one can be told."""
    reach = _reach_sets(items)
    out: list[Finding] = []
    for ida in sorted(scope):
        for idb in sorted(scope):
            if ida >= idb or not items[ida]["globs"] or not items[idb]["globs"]:
                continue  # an empty-globs side is reported by no_globs, not per pair
            if reaches(reach, ida, idb) or reaches(reach, idb, ida):
                continue  # the order already exists: no finding at all
            hits = [
                f"{ga!r} / {gb!r}"
                for ga in items[ida]["globs"]
                for gb in items[idb]["globs"]
                if globs_overlap(ga, gb)
            ]
            if not hits:
                continue
            edge, why = propose(items, band, ida, idb)
            detail = (
                f"{ida} and {idb} both touch {hits[0]}: one of them should need the other, "
                f"or their globs should be narrowed"
            )
            proposal = f"{edge[0]} needs {edge[1]}" if edge else f"no proposed edge ({why})"
            fix: dict[str, Any] | None = None
            if edge and why == "forced":
                fix = {"needs_add": [edge[0], edge[1]]}
            out.append(Finding("overlap", (ida, idb), f"{detail}; {proposal}", fix))
    return out


def no_globs(items: Mapping[str, Any], scope: Iterable[str]) -> list[Finding]:
    """A task with no declared globs collides with EVERY writer; no edge orders that."""
    return [
        Finding(
            "no_globs",
            (iid,),
            f"{iid} declares no globs: every other task may touch the same files and "
            f"nothing will say so (`ddflow update {iid} --globs <comma,separated>`)",
        )
        for iid in sorted(scope)
        if not items[iid]["globs"]
    ]


def stale_needs(items: Mapping[str, Any], scope: Iterable[str]) -> list[Finding]:
    """A `needs` naming an item that is settled (done or abandoned) or gone: already
    satisfied; dropping it can only let the item start, never collide with finished work."""
    return [
        Finding(
            "stale_needs",
            (iid, dep),
            f"{iid} needs {dep!r}, which is "
            + (
                "settled: the edge is already satisfied and names finished work"
                if dep in items
                else "gone: the edge names no item in the queue"
            ),
            {"needs_remove": [dep]},
        )
        for iid in sorted(scope)
        for dep in _needs(items[iid])
        if dep not in items or items[dep]["terminal"]
    ]


def stale_refs(items: Mapping[str, Any], scope: Iterable[str]) -> list[Finding]:
    """A body naming a settled item: evidence pointing at work that is over."""
    out = []
    for iid in sorted(scope):
        for tok in sorted(set(_ID_TOKEN.findall(str(items[iid].get("body", ""))))):
            if tok in items and items[tok]["terminal"]:
                out.append(
                    Finding(
                        "stale_ref",
                        (iid, tok),
                        f"{iid}'s body still names {tok}, which is settled: the reference "
                        f"points at work that is over",
                    )
                )
    return out


def dx_priority(
    items: Mapping[str, Any],
    scope: Iterable[str],
    band: Callable[[str, tuple[str, ...]], str],
    band_priority: Callable[[str, tuple[str, ...]], int],
) -> list[Finding]:
    """A DX or bug-fix task at the generic default priority, behind every feature."""
    return [
        Finding(
            "dx_priority",
            (iid,),
            f"{iid} is a {band(iid, tags)} task at the generic priority "
            f"{int(items[iid]['priority'])}",
            {"priority": band_priority(iid, tags)},
        )
        for iid in sorted(scope)
        for tags in [tuple(str(t) for t in items[iid].get("tags", ()))]
        if band(iid, tags) and int(items[iid]["priority"]) == DEFAULT_PRIORITY
    ]


def body_refs(items: Mapping[str, Any], scope: Iterable[str]) -> list[Finding]:
    """A body naming an OPEN item's id with no ordering edge between them: a reference
    nothing enforces. Report-only: bodies mention context, not dependencies."""
    reach = _reach_sets(items)
    out: list[Finding] = []
    for iid in sorted(scope):
        for tok in sorted(set(_ID_TOKEN.findall(str(items[iid].get("body", ""))))):
            if tok == iid or tok not in items or items[tok]["terminal"]:
                continue
            if reaches(reach, iid, tok) or reaches(reach, tok, iid):
                continue  # the edge exists: the mention IS the dependency
            out.append(
                Finding(
                    "body_ref",
                    (iid, tok),
                    f"{iid}'s body names {tok}, which is open and NOT ordered against it: "
                    f"a missing `needs`, or prose to prune",
                )
            )
    return out


def duplicates(
    items: Mapping[str, Any],
    scope: Iterable[str],
    similar: Callable[[str, str], list[str]] | None,
) -> list[Finding]:
    """Pairs the add-time duplicate hunt would ask about. None when the caller cannot
    provide the check (its machinery is off or unavailable), as in `plancheck`."""
    if similar is None:
        return []
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for iid in sorted(scope):
        for hit in sorted(
            set(similar(str(items[iid].get("title", "")), str(items[iid].get("body", ""))))
        ):
            pair = tuple(sorted((iid, hit)))
            if hit in scope and hit != iid and pair not in seen:
                seen.add(pair)
                out.append(
                    Finding(
                        "duplicate",
                        pair,
                        f"{iid} reads like {hit}, also open: one may BE the other's work",
                    )
                )
    return out


def lint(
    items: Mapping[str, Any],
    *,
    scope: Iterable[str],
    band: Callable[[str, tuple[str, ...]], str] | None = None,
    band_priority: Callable[[str, tuple[str, ...]], int] | None = None,
    similar: Callable[[str, str], list[str]] | None = None,
) -> Report:
    """Every check above. ``scope`` are the ids to lint (live work, narrowed by the caller
    with ``--since``/``--phase``); everything else in ``items`` is read for graph walks.

    ``scope`` is snapshotted once: it feeds seven checks and the headline, and a caller may
    hand it a generator.
    """
    scope = list(scope)
    if band is None:
        def band(_iid, _tags):
            return ""
    if band_priority is None:
        def band_priority(_iid, _tags):
            return DEFAULT_PRIORITY
    report = Report(
        findings=[
            *overlaps(items, scope, band),
            *no_globs(items, scope),
            *stale_needs(items, scope),
            *stale_refs(items, scope),
            *dx_priority(items, scope, band, band_priority),
            *body_refs(items, scope),
            *duplicates(items, scope, similar),
        ],
        items=len(scope),
    )
    report.findings.sort(key=lambda f: (f.kind, f.ids, f.detail))
    return report
