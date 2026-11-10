"""Straggler detection and capped hedging (SW-hedge). Pure and read-only.

Decides which stalled or hard items to duplicate onto another family as a competing
attempt (D-control-planes), capped at a ratio of fleet spend: first branch to pass the
gates merges and the straggler's own attempt is dropped (SW-Q5). Every figure arrives as an
argument -- a caller that already read the state passes it in -- so the plan is replayable
and deterministic: no I/O, no clock, no randomness, no model call. A figure that cannot be
told stays absent, never a guess.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

REASON_STRAGGLER = "straggler"
REASON_HARD = "hard"

DEFAULT_CAP_RATIO = 0.10  # SW-Q5: duplicates capped at 10% of fleet spend


@dataclass(frozen=True)
class Hedge:
    """One duplicate to dispatch: ``item`` raced on ``family`` because of ``reason``."""

    item: str
    family: str = ""  # the competing family the hedge dispatches onto
    reason: str = ""  # ``straggler`` or ``hard``


def stragglers(ages: Mapping[str, float], threshold_s: float) -> tuple[str, ...]:
    """Items whose running age meets ``threshold_s``, sorted by item id."""
    return tuple(sorted(i for i, s in ages.items() if s >= threshold_s))


def candidates(
    ages: Mapping[str, float], threshold_s: float, hard: Collection[str] = ()
) -> tuple[str, ...]:
    """The items a hedge may race: stragglers plus items tagged hard, deduplicated and sorted."""
    merged = set(stragglers(ages, threshold_s))
    merged.update(hard)
    return tuple(sorted(merged))


def budget(fleet_usd: float | None, ratio: float = DEFAULT_CAP_RATIO) -> float | None:
    """The spend cap on duplicates: ``ratio`` of fleet spend. ``None`` when the spend is
    unverified -- the cap is then a floor the caller reports, not a number to guess."""
    if fleet_usd is None:
        return None
    return fleet_usd * ratio


def plan(
    candidates: Sequence[str],
    on: Mapping[str, Collection[str]],
    families: Sequence[str],
    hard: Collection[str] = (),
    *,
    budget_usd: float | None = None,
    attempt_usd: float | None = None,
) -> tuple[Hedge, ...]:
    """The capped hedge plan.

    Each candidate is duplicated onto the first ``families`` not already racing it (a hedge
    never lands on the item's own family); a candidate with no other family is skipped. The
    plan stops once the next attempt would exceed ``budget_usd``; when either the budget or
    ``attempt_usd`` is unverified the cap is left unchecked instead of guessed.
    """
    out: list[Hedge] = []
    spent = 0.0
    for item in candidates:
        already = set(on.get(item, ()))
        family = next((f for f in families if f not in already), "")
        if not family:
            continue
        if budget_usd is not None and attempt_usd is not None and spent + attempt_usd > budget_usd:
            break
        out.append(Hedge(item, family, REASON_HARD if item in hard else REASON_STRAGGLER))
        spent += attempt_usd or 0.0
    return tuple(out)
