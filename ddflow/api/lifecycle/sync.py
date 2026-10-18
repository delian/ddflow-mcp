"""`sync`: bring an item's branch up to date with its recorded base, safely.

Part of `ddflow.api.lifecycle` by file, not by import: the declared command calls this
module, so no registry names it. The calculation is `services.branchsync`."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...infra import worktree as W
from ...services import branchsync as BS
from ...services.gates import outcomes as GO
from .._base import _load


def sync(repo: Path, *, item: str = "", check: bool = False, agent: str = "") -> O.Outcome:
    _log, cfg, st = _load(repo, agent)
    if item:
        it = st.items.get(item)
        if it is None or it.removed:
            return O.failed("sync", f"no such item {item!r}")
    else:
        it = BS.tree_item(st, repo, _here())
        if it is None:
            return O.failed(
                "sync", "this is not an item's worktree: name the item, `ddflow sync ITEM`"
            )
    if not it.worktree:
        return O.failed("sync", f"{it.id} has no worktree to bring up to date")
    tree = W.load_path(repo, it.worktree)
    r = BS.sync(repo, cfg, tree, it.base, list(it.globs), check=check)
    data = {
        "item": it.id,
        "base": r.base,
        "status": r.status,
        "behind": r.behind,
        "touching_globs": r.relevant,
        "pin_files": r.merged_pins,
        "conflicts": r.conflicts,
        "dirty": r.dirty,
        "detail": r.detail,
    }
    if r.status == "merged":
        notes = GO.stale_evidence_detail(st, cfg, it.id, tree, landed="HEAD")
        stale = {n.gate for n in notes if not n.unverified}
        unverified = {n.gate for n in notes if n.unverified}
        recorded = {g for g in it.gates if it.gates[g].outcome}
        data["gates_valid"] = sorted(recorded - stale - unverified)
        data["gates_stale"] = sorted(recorded & stale)
        data["gates_unverified"] = sorted(recorded & unverified)
    text = _text(r, data)
    if r.status in ("refused", "conflict"):
        return O.refused("sync", text, **data)
    if r.status in ("unknown", "current"):
        return O.nothing("sync", text, **data)
    return O.ok("sync", text=text, **data)


def _here() -> Path:
    """The checkout the caller stands in (a linked worktree, not the primary)."""
    top = W.git(Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(top.out) if top.ok and top.out else Path.cwd()


def _text(r: BS.SyncResult, d: dict) -> str:
    if r.status == "current":
        return f"{d['item']} is up to date with `{r.base}`."
    if r.status == "unknown":
        return f"could not tell how far {d['item']} is behind `{r.base}`: {r.detail}"
    if r.status == "refused":
        files = "".join(f"\n  {p}" for p in r.dirty[:20])
        return f"not synced: {r.detail}{files}"
    if r.status == "conflict":
        lines = "".join(
            f"\n  {p}  (last changed on `{r.base}` by {w})" for p, w in r.conflicts.items()
        )
        return (
            f"merge of `{r.base}` conflicts; aborted, {d['item']}'s branch is as it was. "
            f"Conflicting files:{lines}"
        )
    head = f"{d['item']} is {r.behind} commit(s) behind `{r.base}`; {r.relevant} touch its globs."
    if r.status == "behind":
        return head + f" `ddflow sync {d['item']}` merges them."
    pins = f" Pin files settled: {', '.join(r.merged_pins)}." if r.merged_pins else ""
    valid, stale = d.get("gates_valid", []), d.get("gates_stale", [])
    unver = d.get("gates_unverified", [])
    extra = f"; could not be checked: {', '.join(unver)}" if unver else ""
    return (
        f"merged `{r.base}` into {d['item']}: {r.behind} commit(s), {r.relevant} touching its globs."
        f"{pins} Recorded gates still valid: {', '.join(valid) or 'none'}; stale: {', '.join(stale) or 'none'}{extra}."
    )
