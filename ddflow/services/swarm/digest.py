"""The swarm section of the operator digest (SW-digest). Pure and read-only.

Folds the fleet picture the operator digest reports (P-operator-digest, D-OD-Q1:
``docs/ddflow/decisions/operator-digest.md`` section 4 ``OD-digest``): lanes and their
families, who holds what, an ETA from the critical path, spend per merged item, hedges in
flight, and the dispatch note that says why a lane got its item.

Every figure arrives as an argument -- a caller that already read the state and the log
passes it in -- so the section is replayable and deterministic: no I/O, no clock, no
randomness, no model call. A figure that cannot be told stays ``None`` (``unavailable``),
never a guess, per the operator digest's own rule (a labelled range or ``unavailable``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MTOK = 1_000_000.0


@dataclass(frozen=True)
class Lane:
    name: str
    family: str = ""
    model: str = ""


@dataclass(frozen=True)
class Holder:
    lane: str
    item: str
    family: str = ""


@dataclass(frozen=True)
class Hedge:
    item: str
    families: tuple[str, ...] = ()


@dataclass(frozen=True)
class Spend:
    item: str
    usd: float | None  # None: unavailable -- a needed price or token count is unverified
    family: str = ""
    model: str = ""


@dataclass(frozen=True)
class Why:
    lane: str
    item: str
    note: str = ""


@dataclass(frozen=True)
class Section:
    lanes: tuple[Lane, ...] = ()
    holders: tuple[Holder, ...] = ()
    eta_s: float | None = None  # None: unavailable -- a path item has no duration estimate
    eta_method: str = "critical-path"
    hedges: tuple[Hedge, ...] = ()
    spend: tuple[Spend, ...] = ()
    why: tuple[Why, ...] = ()


def eta_from_path(
    chain: Sequence[str], durations: Mapping[str, float]
) -> tuple[float | None, str]:
    """Remaining wall-clock from the critical path: the sum of each path item's duration
    estimate. A path item with no estimate makes the figure unavailable -- the path is a
    floor and a gap in it would understate it, so the answer is None, not a low guess."""
    total = 0.0
    for item in chain:
        if item not in durations:
            return None, "critical-path"
        total += durations[item]
    return total, "critical-path"


def hedges_from(attempts: Mapping[str, Sequence[str]]) -> tuple[Hedge, ...]:
    """Hedges in flight: items two or more distinct families race as competing attempts.
    Returns rows sorted by item id; a single-family item is no hedge."""
    rows = []
    for item in sorted(attempts):
        families = tuple(dict.fromkeys(a for a in attempts[item] if a))  # distinct, ordered
        if len(families) >= 2:
            rows.append(Hedge(item, families))
    return tuple(rows)


def spend_usd(
    est_in: float,
    est_out: float,
    price_in: float | None,
    price_out: float | None,
    *,
    local: bool = False,
) -> float | None:
    """Estimated USD for one merged item's author route. Free on a local route (0); None
    when a token estimate is used but its price is unverified (``local`` has no price)."""
    if local:
        return 0.0
    if est_in and price_in is None:
        return None
    if est_out and price_out is None:
        return None
    return (est_in * (price_in or 0.0) + est_out * (price_out or 0.0)) / MTOK


def section(
    lanes: Sequence[Lane] = (),
    holders: Sequence[Holder] = (),
    chain: Sequence[str] = (),
    durations: Mapping[str, float] | None = None,
    *,
    hedges: Sequence[Hedge] = (),
    spend: Sequence[Spend] = (),
    why: Sequence[Why] = (),
) -> Section:
    """The swarm digest section from figures a caller already read. ``chain`` and
    ``durations`` give the ETA; a ``durations`` of None means the caller could not measure
    one, so the ETA is unavailable."""
    dur = durations if durations is not None else {}
    eta_s, method = eta_from_path(list(chain), dur)
    return Section(
        lanes=tuple(lanes),
        holders=tuple(holders),
        eta_s=eta_s,
        eta_method=method,
        hedges=tuple(hedges),
        spend=tuple(spend),
        why=tuple(why),
    )
