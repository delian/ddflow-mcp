"""A failed review gate is not a finished one (decision D-failed-review-gate, bug B85841c0846).

`merge` and `complete` refuse a review gate whose latest outcome is failed unless every
confirmed finding is followed by a later delta round that did not raise it again."""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Config
from ...core.model import State
from .defs import DEFAULT_GATES, GateDef, pipeline_for
from .outcomes import (
    _cleared_after_delta,  # noqa: F401  (re-exported for api.review.context/records)
    unresolved_review_failure,
)

#: How a blocker of `failed_review_blockers` begins; `complete` never lifts these with
#: `--force` (decision D-failed-review-gate): only the operator's `gate skip --reason` does.
FAILED_REVIEW_BLOCKER = "review gate(s) failed with findings not re-reviewed: "


def failed_review_blockers(
    state: State,
    cfg: Config,
    item_id: str,
    defs: Mapping[str, GateDef] | None,
) -> list[str]:
    """One blocker for the review gates (any gate with a `reviewer`) of ``item_id``'s
    pipeline that are FAILED and unanswered (`unresolved_review_failure`). The operator's way past is `gate skip --reason`."""
    it = state.items.get(item_id)
    if it is None:
        return []
    rows = []
    for g in pipeline_for(it, cfg):
        gd = (defs or {}).get(g) or DEFAULT_GATES.get(g)
        if gd is None or not gd.reviewer:
            continue
        if why := unresolved_review_failure(it, g):
            rows.append(f"{g} ({why})")
    if not rows:
        return []
    # The re-run a refusal names must be one that actually works (B-merge-refusal-says-delta-
    # is-free-of-max-rounds): `review triage` is always allowed, a true fix round is free of
    # max_rounds only when [review].fix_round_free allows it AND it addresses every finding,
    # and a plain re-run past the cap is refused -- so name the forced re-run, never a plain
    # one review will then refuse.
    fix_hint = (
        " (a fix round that addresses every finding is free of max_rounds)"
        if cfg.review.fix_round_free
        else ""
    )
    return [
        FAILED_REVIEW_BLOCKER
        + "; ".join(rows)
        + f". Fix, then settle each finding with `ddflow review triage {item_id} --gate <gate>` "
        f"(always allowed) and, if the code changed, re-review with `ddflow review {item_id} "
        f'--gate <gate> --force --reason "..."`{fix_hint}, or the OPERATOR records the reason '
        f"with `ddflow gate skip {item_id} <gate> --reason ...` (a merge/complete `--force` "
        "does not lift it)."
    ]
