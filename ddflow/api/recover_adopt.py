"""`recover --item ID --adopt --reason WHY`: take over an EXPIRED lease without a force flag
(GR-lease-takeover).

An agent's forced claim is dropped (B49fa10bbb1), so salvaging a dead lane needed an operator's
approval even when nothing could be lost. Adoption is the safe case made explicit: the expired
lease's tree is MEASURED to hold no uncommitted file (committed work stays on its branch and is
bound with the tree), a reason is given, and the lease note records who took over, from whom
and why. A tree that could not be measured, one with uncommitted work, a live lease and a
missing reason all refuse and change nothing."""

from __future__ import annotations

import importlib
import time
from pathlib import Path

from ..core import outcome as O
from ..services import leases as L
from ._base import _load

KIND = "recover.adopt"


def _refuse(reason: str, item: str) -> O.Outcome:
    return O.refused(KIND, reason, id=item)


def adopt(repo: Path, item: str, *, reason: str = "", agent: str = "") -> O.Outcome:
    """Adopt ``item``'s expired lease for the caller; see the module docstring."""
    CL = importlib.import_module(
        "ddflow.api.lifecycle.claim"
    )  # lazy: lifecycle.claim imports the reporting package

    if not item:
        return _refuse("--adopt names the lease to take over: give --item ID", item)
    if not (reason or "").strip():
        return _refuse("--adopt needs a --reason: it is recorded with who took over", item)
    log, cfg, st = _load(repo, agent)
    it = st.items.get(item)
    lease = it.lease if it is not None else None
    if lease is None:
        return _refuse(f"{item} holds no lease to adopt", item)
    if lease.holder == log.agent_id:
        return _refuse(f"{item}'s lease is already yours: `ddflow claim {item}`", item)
    if lease.live(time.time(), cfg.lease.grace_s):
        return _refuse(
            f"{item}'s lease is live (held by {lease.holder}): wait for it to expire or "
            "ask the holder to release it",
            item,
        )
    rec = next(
        (
            r
            for r in L.scan(log, cfg, repo, state=st)
            if r.item == item and r.kind == "expired_lease"
        ),
        None,
    )
    if rec is None or rec.salvageable is None:
        return _refuse(
            f"could not measure {item}'s tree: nothing is adopted that could not be shown clean",
            item,
        )
    if rec.dirty_files:
        return _refuse(
            f"{item}'s tree holds {rec.dirty_files} uncommitted file(s): commit or salvage them "
            "first (the operator's route is `ddflow claim --approval request`)",
            item,
        )
    note = (
        f"adopted the expired lease of {lease.holder} "
        f"({rec.unmerged_commits} unmerged commit(s) kept on its branch): {reason.strip()}"
    )
    out = CL._claim_once(repo, item, note=note, force=True, agent=agent)
    # the recover surface's output caps and renderer read these keys
    out.data.setdefault("found", [])
    out.data.setdefault("_render", {"found": [], "salvageable": []})
    out.data.setdefault("count", 1)
    return out
