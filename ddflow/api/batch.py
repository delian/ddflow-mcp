"""Many item edits as ONE decision: `batch` and `fold` (DX-task-fold).

Every edit is planned against the same state before anything is recorded (`items.plan_edit`,
the function `update` records), the dependency graph is checked for a cycle the batch would
CREATE, and then the events go in under one lock. A refusal anywhere records nothing; a dry
run shows the diff and records nothing either. `fold` is a batch whose last step abandons the
duplicate -- the engine is this one function, for every surface.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from ..config import csv_list
from ..core import outcome as O
from ..core.graph import find_cycles
from ..services import leases as L
from ._base import _load
from .items import ItemEdit, plan_edit

#: ItemEdit fields a batch entry may not set: they act on the checkout, not on the plan.
_NOT_BATCHABLE = ("worktree", "queue")
_LISTS = (
    "needs",
    "add_needs",
    "remove_needs",
    "tags",
    "add_tags",
    "remove_tags",
    "resources",
)


def _edit_of(entry: dict[str, Any]) -> ItemEdit | str:
    """An `ItemEdit` from one batch entry, or why it is not one."""
    known = {f.name for f in dataclasses.fields(ItemEdit)} - set(_NOT_BATCHABLE)
    bad = sorted(set(entry) - known - {"id"})
    if bad:
        return f"unknown or unbatchable field(s) {bad}; allowed: {sorted(known)}"
    values = {
        k: (csv_list(v) if k in _LISTS and isinstance(v, str) else v) for k, v in entry.items()
    }
    values.pop("id")
    return ItemEdit(**values)


def _cycles(needs: dict[str, list[str]]) -> set[tuple[str, ...]]:
    return {tuple(c) for c in find_cycles(needs, edges=lambda n: n)}


def _plan(cfg, st, entries, abandon, kind):
    """``(plans, unchanged)`` -- each plan ``(item, fields)`` -- or the Outcome that stops the batch."""
    seen: set[str] = set()
    plans: list[tuple[Any, dict[str, Any]]] = []
    unchanged: list[str] = []
    for n, entry in enumerate(entries, 1):
        iid = entry.get("id") if isinstance(entry, dict) else None
        if not iid:
            return O.failed(kind, f"entry {n} has no 'id'")
        if iid in seen:
            return O.failed(kind, f"{iid} appears twice: merge its edits into one entry", id=iid)
        seen.add(iid)
        it = st.items.get(iid)
        if it is None or it.removed:
            return O.failed(kind, f"entry {n}: no such item {iid!r}", id=iid)
        if it.terminal:
            return O.refused(kind, f"{iid} is already {it.state}; a batch edits live items", id=iid)
        edit = _edit_of(entry)
        if isinstance(edit, str):
            return O.failed(kind, f"entry {n} ({iid}): {edit}", id=iid)
        fields = plan_edit(cfg, st, it, edit)
        if isinstance(fields, O.Outcome):
            if fields.exit != O.NOTHING:
                return O.Outcome(
                    kind=kind, data={"id": iid}, exit=fields.exit, reason=fields.reason
                )
            unchanged.append(iid)
            continue
        plans.append((it, fields))
    return plans, unchanged


def _cycle_refusal(st, plans, abandon, kind) -> O.Outcome | None:
    """The refusal when the planned needs would create a dependency cycle that is not there now."""
    needs = {i: list(x.needs) for i, x in st.live_by_id().items()}
    before = _cycles(needs)
    for it, fields in plans:
        if "needs" in fields and it.id in needs:
            needs[it.id] = list(fields["needs"])
    for gone in abandon:
        needs.pop(gone, None)
    new = sorted(c for c in _cycles(needs) if c not in before)
    if not new:
        return None
    shown = "; ".join(" -> ".join(c) for c in new[:3])
    return O.refused(
        kind, f"the batch would create a dependency cycle: {shown}", cycles=[list(c) for c in new]
    )


def _record(log, cfg, st, plans, abandon, kind, result) -> O.Outcome:
    """Append the planned events under one lock; a lease refusal records nothing."""
    with log.transaction():
        retargets = []
        for it, fields in plans:
            globs, res = fields.get("globs"), fields.get("resources")
            if globs is None and res is None:
                continue
            lease, refusal = L.plan_retarget(log, cfg, it.id, globs, res)
            if refusal:
                return O.refused(kind, refusal, id=it.id, **result)
            retargets.append((it, lease, globs, res))
        for it, fields in plans:
            log.append(f"{it.family}.updated", it.id, fields)
        for it, lease, globs, res in retargets:
            if lease is not None:
                L.retarget(log, it.id, lease, globs, res)
        for iid, why in abandon.items():
            ab = st.items[iid]
            log.append("item.abandoned", iid, {"reason": why, "kind": ab.kind})
            if ab.lease:
                L.release(log, iid, note=f"abandoned: {why}")
    return O.ok(kind, **result)


def apply(
    repo: Path,
    entries: list[dict[str, Any]],
    *,
    abandon: dict[str, str] | None = None,
    dry_run: bool = False,
    agent: str = "",
    kind: str = "item.batch",
) -> O.Outcome:
    """Apply ``entries`` (``{"id": ..., <ItemEdit field>: ...}``) and abandon the items in
    ``abandon`` (id -> reason), all or nothing. ``dry_run`` plans and records nothing."""
    abandon = abandon or {}
    log, cfg, st = _load(repo, agent)
    planned = _plan(cfg, st, entries, abandon, kind)
    if isinstance(planned, O.Outcome):
        return planned
    plans, unchanged = planned
    refusal = _cycle_refusal(st, plans, abandon, kind)
    if refusal is not None:
        return refusal
    result = {
        "dry_run": dry_run,
        "edits": [
            {
                "id": it.id,
                "changed": sorted(f),
                "fields": f,
                "before": {k: getattr(it, k, None) for k in f},
            }
            for it, f in plans
        ],
        "unchanged": unchanged,
        "abandoned": sorted(abandon),
    }
    if dry_run:
        return O.ok(kind, **result)
    return _record(log, cfg, st, plans, abandon, kind, result)


def _fold_entries(st, a, b, why: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The batch entries that fold ``a`` into ``b``, and the dependents it re-points."""
    dup, keep = a.id, b.id
    note = f"Folded {dup} ({a.title}): {why}." + (f"\n\n{a.body}" if a.body else "")
    entries: dict[str, dict[str, Any]] = {keep: {"id": keep, "append_body": note}}
    if a.globs:
        entries[keep]["add_globs"] = list(a.globs)
    repointed: list[str] = []
    for it in st.live_items():
        if it.id == dup or it.terminal or dup not in it.needs:
            continue
        moved = [n for n in dict.fromkeys(keep if n == dup else n for n in it.needs) if n != it.id]
        entries.setdefault(it.id, {"id": it.id})["needs"] = moved
        repointed.append(it.id)
    return entries, repointed


def fold(
    repo: Path, dup: str, keep: str, *, reason: str = "", dry_run: bool = False, agent: str = ""
) -> O.Outcome:
    """Fold the duplicate ``dup`` into the survivor ``keep`` in one transaction: ``dup``'s
    body is appended to ``keep``'s, its globs are unioned in, every live item that needs
    ``dup`` is re-pointed to need ``keep``, and ``dup`` is abandoned."""
    kind = "task.fold"
    _log, _cfg, st = _load(repo, agent)
    if dup == keep:
        return O.failed(kind, "a task cannot be folded into itself", id=dup)
    a, b = st.items.get(dup), st.items.get(keep)
    for iid, it in ((dup, a), (keep, b)):
        if it is None or it.removed:
            return O.failed(kind, f"no such item {iid!r}", id=iid)
    if a.terminal or b.terminal:
        done = dup if a.terminal else keep
        return O.refused(
            kind, f"{done} is already {st.items[done].state}; fold live tasks", id=done
        )
    if a.lease or a.worktree:
        return O.refused(
            kind,
            f"{dup} is claimed; release it (`ddflow release {dup}`) before folding it away",
            id=dup,
        )
    why = reason or f"duplicate of {keep}"
    entries, repointed = _fold_entries(st, a, b, why)
    out = apply(
        repo,
        list(entries.values()),
        abandon={dup: f"folded into {keep}: {why}"},
        dry_run=dry_run,
        agent=agent,
        kind=kind,
    )
    if out.data is not None and out.exit == O.OK:
        out.data.update(dup=dup, keep=keep, repointed=sorted(repointed))
    return out
