"""Who held an item, and as which model: the folds the swarm ledger and the metrics share.

A leaf module (no import of ``metrics`` or ``ledger``), so both can import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...core import progress as PR
from ...core.events import Event


def _models(events: Sequence[Event]) -> dict[str, list[tuple[float, str]]]:
    """agent -> [(time, model)] from ``session.started``, oldest first."""
    out: dict[str, list[tuple[float, str]]] = {}
    for ev in events:
        d = ev.data if isinstance(ev.data, dict) else {}
        if ev.kind == "session.started":  # a model-less session is a marker: it ends the old one
            m = d.get("model") if isinstance(d.get("model"), str) else ""
            out.setdefault(ev.agent, []).append((PR.epoch(ev.ts), m))
    for rows in out.values():
        rows.sort(key=lambda r: r[0])  # stable: equal times keep log order
    return out


def _lease_models(events: Sequence[Event]) -> dict[tuple[str, str], list[tuple[float, str]]]:
    """(item, holder) -> [(time, model)] per ``lease.acquired``; "" when it carried none."""
    out: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for ev in events:
        d = ev.data if isinstance(ev.data, dict) else {}
        if ev.kind == "lease.acquired":
            m = d.get("model") if isinstance(d.get("model"), str) else ""
            out.setdefault((ev.subject, str(d.get("holder") or ev.agent)), []).append(
                (PR.epoch(ev.ts), m)
            )
    for rows in out.values():
        rows.sort(key=lambda r: r[0])
    return out


def _author_model(item: str, agent: str, claim: float, sessions: dict, leases: dict) -> str:
    """The model the author held at ``claim``: the lease's own, else their latest session's."""
    held = [m for t, m in leases.get((item, agent), ()) if t <= claim]
    if held and held[-1]:
        return held[-1]
    seen = [m for t, m in sessions.get(agent, ()) if t <= claim]
    return seen[-1] if seen else ""
