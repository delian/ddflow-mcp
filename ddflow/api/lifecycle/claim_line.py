"""A refused claim's place in line (`waits.queue`) and giving it up once the claim lands.

Part of `ddflow.api.lifecycle`; split from `claim` to keep that module under its size cap."""

from __future__ import annotations

import time
from pathlib import Path

from ...core import outcome as O
from ...services import leases as L
from ...services import waits as WT


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


def _with_notes(out: O.Outcome, notes: list) -> O.Outcome:
    """What claim guards want said about a claim that went ahead, on its answer."""
    if notes:
        out.data["claim_notes"] = [n.as_dict() for n in notes]
    return out


def _prior_lease(st, cfg, item: str, me: str):
    """(the item's lease as folded, whether it is a LIVE one of ``me``'s). A holder
    re-claiming its own live lease only renews it."""
    it = st.items.get(item)
    prior = it.lease if it is not None else None
    live = bool(prior and prior.holder == me and prior.live(time.time(), cfg.lease.grace_s))
    return prior, live


def _blocking_items(st, cfg, item: str, want, me: str) -> list[str]:
    """The held items a refused claim of ``item`` is waiting behind: what `waiters` names."""
    it = st.items.get(item)
    if it is None:
        return [item]
    now = time.time()
    clash = L.glob_clash(st, cfg, it, me, list(want if want is not None else it.globs), now)
    return [clash[0]] if clash else [item]
