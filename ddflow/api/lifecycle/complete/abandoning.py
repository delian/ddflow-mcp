"""`abandon`: stop work without completing, with a recorded reason.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

import importlib
from pathlib import Path

from ... import items as _items
from ....core import outcome as O
from ....core.model import DONE, REVIEW, fold
from ....core.stuck import stuck_edges_to
from ....services import leases as L
from ....services.gates import skip_followup as SF
from ..._base import _load_fresh
from .._common import _require
from .dispose import _dispose


def _abandon_refused(item: str, reason: str, why: str) -> O.Outcome:
    """Built directly: `O.refused(kind, reason, **data)` owns `reason`, so passing the
    abandon reason as a wire field raised TypeError -- `abandon` on a DONE item crashed
    with exit 1 instead of refusing with exit 3, and the refusal text was never shown."""
    return O.Outcome(
        kind="item.abandoned", data={"id": item, "reason": reason}, exit=O.REFUSED, reason=why
    )


def _drop_needed_edges(repo: Path, item: str, stranded, agent: str) -> O.Outcome | None:
    """Drop the edge to ``item`` from every live item that still needs it, or the outcome
    of the first drop that fails (``None`` when every drop landed)."""
    for dep_id, _ in stranded:
        out = _items.update(repo, dep_id, _items.ItemEdit(remove_needs=[item]), agent=agent)
        if out.exit != O.OK:
            return out
    return None


def abandon(
    repo: Path,
    item: str,
    *,
    reason: str = "",
    force: bool = False,
    repoint_to: str = "",
    drop_edges: bool = False,
    agent: str = "",
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
) -> O.Outcome:
    """Stop work without completing, with a recorded reason.

    Distinct from `block`: a blocked item is waiting for something and will resume, an
    abandoned one will not. The phase completion check treats only `done` and `abandoned`
    as settled, so an item you decided against stops holding its phase open — which it
    otherwise does forever, since nothing else can ever finish it.
    """
    log, cfg, st = _load_fresh(repo, agent)
    it = _require(st, item, "item.abandoned", cfg.lease.grace_s, log.clone_id(create=False))
    if isinstance(it, O.Outcome):
        return it
    if it.state == REVIEW and it.pr and it.pr.state == "open" and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} has an open request ({it.pr.url}). Abandoning it here leaves that "
            f"request open -- mergeable by anyone, recorded by no one -- and anything "
            f"stacked on it would be re-based onto the target carrying its commits. Close "
            f"the request (`pr sync` then parks it), or --force.",
        )
    if it.state == DONE and not force:
        return _abandon_refused(
            item,
            reason,
            f"{item} is already done; abandoning it would rewrite finished history. "
            f"--force if you really mean it.",
        )
    repointed: list[str] = []
    if repoint_to:
        moved = _rename().repoint(repo, item, repoint_to, agent=agent)
        if moved.exit not in (O.OK, O.NOTHING):
            return moved
        repointed = [e["id"] for e in moved.data.get("edits", [])]
        st = fold(log.read_all(), strict=False)
    stranded = stuck_edges_to(st, item)
    if stranded and not repoint_to and not drop_edges:
        names = ", ".join(i for i, _ in stranded)
        return _abandon_refused(
            item,
            reason,
            f"{item} is still needed by {names}. Re-point them to another id "
            f"(--repoint-to ID) or drop the edge to the abandoned item (--drop-edges), "
            f"so none is left waiting on it.",
        )
    if drop_edges:
        failed = _drop_needed_edges(repo, item, stranded, agent)
        if failed is not None:
            return failed
        stranded = []
    log.append("item.abandoned", item, {"reason": reason, "kind": it.kind})
    SF.settle(log, st, cfg, item)
    if it.lease:
        L.release(log, item, note=f"abandoned: {reason}")
    return O.Outcome(
        kind="item.abandoned",
        data={
            "id": item,
            "reason": reason,
            "stranded": [i for i, _ in stranded],
            "drop_edges": [c for _, c in stranded],
            "repointed": repointed,
            **_dispose(repo, cfg, log, item, (called_from, shell_cwd)),
        },
    )


def _rename():
    """`api/rename.py`, loaded at call time (it imports the batch engine)."""
    return importlib.import_module("ddflow.api.rename")
