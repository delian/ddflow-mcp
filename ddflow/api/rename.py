"""`rename OLD NEW`: move an item to a new id (and phase), repointing every needs edge (DX-rename-item).

Item ids are event keys, so a rename is recorded as what it is: the item is re-filed under
the new id with the same title, body, globs, resources, tags, priority and needs, every live
item that needed the old id is re-pointed in the same locked step, and the old id stays as an
abandoned tombstone whose reason names the new one. Nothing is erased. `repoint` is the
dependents half alone, for `abandon --repoint-to`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import items as IT
from . import batch
from ._base import _load


def _dependents(st, old: str) -> list:
    return [i for i in st.live_items() if not i.terminal and old in i.needs]


def _moved(needs: list[str], old: str, new: str, own: str) -> list[str]:
    return [n for n in dict.fromkeys(new if n == old else n for n in needs) if n != own]


def _problem(st, it, old: str, new: str, parent: str | None) -> O.Outcome | None:
    kind = "item.renamed"
    if it is None or it.removed:
        return O.failed(kind, f"no such item {old!r}", id=old)
    if old == new:
        return O.failed(kind, "the new id is the old one", id=old)
    if it.kind == "phase":
        return O.refused(kind, f"{old} is a phase; rename moves tasks", id=old)
    if it.terminal:
        return O.refused(kind, f"{old} is already {it.state}; nothing to rename", id=old)
    if it.lease or it.worktree or it.branch or it.pr:
        return O.refused(
            kind,
            f"{old} is claimed or has a branch or request; release it (`ddflow release {old}`) "
            f"and dispose of those first",
            id=old,
        )
    kids = [c.id for c in st.live_items() if c.parent == old]
    if kids:
        return O.refused(
            kind, f"{old} has sub-tasks ({', '.join(kids[:5])}); move them first", id=old
        )
    bad = IT.bad_id(new) or IT.taken(st, new, readd=False)
    if bad:
        return O.failed(kind, bad, id=old)
    bad = IT.missing_parent(st, parent)
    return O.failed(kind, bad, id=old) if bad else None


def rename(
    repo: Path,
    old: str,
    new: str,
    *,
    parent: str = "",
    reason: str = "",
    dry_run: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Re-file ``old`` as ``new`` (under ``parent`` when given), re-pointing its dependents
    and abandoning ``old`` as a tombstone, in one locked step."""
    kind = "item.renamed"
    log, _cfg, _st = _load(repo, agent)
    with log.transaction():
        log, cfg, st = _load(repo, agent)
        it = st.items.get(old)
        refusal = _problem(st, it, old, new, parent)
        if refusal is not None:
            return refusal
        deps = _dependents(st, old)
        data: dict[str, Any] = {
            "old": old,
            "new": new,
            "parent": parent or it.parent,
            "repointed": sorted(d.id for d in deps),
            "dry_run": dry_run,
        }
        if dry_run:
            return O.ok(kind, **data)
        draft = IT.TaskDraft(
            new,
            title=it.title,
            parent=parent or it.parent or None,
            needs=list(it.needs),
            globs=list(it.globs),
            resources=list(it.resources) or None,
            body=it.body,
            tags=list(it.tags),
            priority=it.priority,
            line=it.line or None,
            kind=it.kind if it.kind != "task" else None,
            extra={"fixes": list(it.fixes)} if getattr(it, "fixes", None) else {},
        )
        added = IT.add_task(log, st, cfg, draft, dedupe=f"renamed from {old}")
        if not added.ok:
            return (O.refused if added.refused else O.failed)(kind, added.problem, id=old)
        for d in deps:
            log.append(f"{d.family}.updated", d.id, {"needs": _moved(d.needs, old, new, d.id)})
        why = reason or f"renamed to {new}"
        log.append("item.abandoned", old, {"reason": f"renamed to {new}: {why}", "kind": it.kind})
    return O.ok(kind, **data)


def repoint(repo: Path, old: str, to: str, *, agent: str = "", dry_run: bool = False) -> O.Outcome:
    """Re-point every live item that needs ``old`` to need ``to``, in one batch (cycles
    refused). The dependents half of a rename, for `abandon --repoint-to`."""
    _log, _cfg, st = _load(repo, agent)
    target = st.items.get(to)
    if target is None or target.removed or target.terminal:
        return O.failed("item.repointed", f"--repoint-to {to!r} is not a live item", id=old)
    entries = [{"id": d.id, "needs": _moved(d.needs, old, to, d.id)} for d in _dependents(st, old)]
    if not entries:
        return O.nothing("item.repointed", f"nothing needs {old}", id=old)
    return batch.apply(repo, entries, dry_run=dry_run, agent=agent, kind="item.repointed")
