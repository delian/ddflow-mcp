"""Poison-model reassignment: which families the scheduler should avoid for an item.

A family that has FAILED an item's gates more than once — two failed leases — is *poisoned*
for that item: the ledger's rework term already knows that family underperforms work of the
item's kind, and handing the scored item back to it only spends a third attempt. Detection
is per ``(item, family)``, not per family: a family two-for-two on one item stays welcome on
any DIFFERENT item it has not failed twice.

``detect`` folds the event log once with ``metrics.facts.collect`` — the very fold the
``swarm_metrics`` view and the quality ledger share — and returns a read-only ``Report`` the
scheduler reads when it chooses a lane for an item: route the item to a family that is NOT
in its poisoned set. The fold credits each failed gate outcome to the family of the lease
that held the item across it (``ledger._author_model``), so a failed review on a lease the
family never held is never charged to it. The threshold is the ``attempts`` parameter,
default 2, overridable per call (the ``[swarm]`` config section that sets it arrives with
the config wiring, which is enough of a knob for this module by itself).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...config import Config
from ...config_sections.agent import family_for
from ...core.events import Event
from ...core.model import State
from ..metrics.facts import collect
from . import ledger as L

#: Default failures that mark a family poisoned for an item (default: two failed leases).
ATTEMPTS = 2


@dataclass(frozen=True)
class Poison:
    """One (item, family) the scheduler avoids: the family failed ``failed`` times there."""

    item: str
    family: str
    failed: int

    def as_dict(self) -> dict[str, str | int]:
        return {"item": self.item, "family": self.family, "failed": self.failed}


@dataclass(frozen=True)
class Report:
    """Per ``(item, family)`` failed-lease counts, and ``attempts``, the mark of poisoning."""

    failed: dict[tuple[str, str], int]
    attempts: int = ATTEMPTS

    def avoids(self, item: str, family: str) -> bool:
        """Is ``family`` poisoned for ``item`` (the scheduler routes elsewhere)?"""
        return bool(family) and self.failed.get((item, family), 0) >= self.attempts

    def route(self, item: str, candidates: Sequence[str]) -> list[str]:
        """``candidates`` without the families poisoned for ``item`` (their order is kept)."""
        return [f for f in candidates if not self.avoids(item, f)]

    def as_list(self) -> list[dict[str, str | int]]:
        return [Poison(i, f, n).as_dict() for (i, f), n in sorted(self.failed.items())]


def detect(
    events: Sequence[Event],
    state: State,
    cfg: Config,
    now: float,
    *,
    attempts: int = ATTEMPTS,
) -> Report:
    """Count failed leases per ``(item, family)`` and mark the poisoned ones.

    A lease is "failed" when the gate outcome that fell inside its hold was ``failed``; an
    ``unavailable`` or ``partial`` outcome is never one (the lane did not prove wrong, the
    tool could not run), exactly as the ledger reads them. ``now`` closes the leases still
    held when the fold runs, so an item in flight is judged on the outcomes recorded so far.
    """
    facts = collect(events, state, now, cfg)
    sessions = L._models(events)
    leases = L._lease_models(events)
    fam_map: dict[str, str] = dict(getattr(cfg.agent, "families", {}) or {})
    gates: dict[str, list] = {}
    for o in facts.outcomes:
        gates.setdefault(o.item, []).append(o)
    failed: dict[tuple[str, str], int] = {}
    for item, span in facts.spans.items():
        done = span.completed_at
        if done is None:
            # in flight: there is no more precise boundary than the fold's ``now`` alone.
            done = now
        claims = list(span.claims)
        for idx, (at, author) in enumerate(claims):
            nxt = claims[idx + 1][0] if idx + 1 < len(claims) else done
            if not any(o.kind == "failed" and at <= o.at < nxt for o in gates.get(item, ())):
                continue
            model = L._author_model(item, author, at, sessions, leases)
            fam = family_for(model, fam_map or None)
            key = (item, fam)
            failed[key] = failed.get(key, 0) + 1
    poisoned = {k: n for k, n in failed.items() if n >= attempts}
    return Report(poisoned, attempts)
