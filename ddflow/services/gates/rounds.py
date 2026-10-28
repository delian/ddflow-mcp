"""Review round counting from ONE fold: the item's gate history (DX-review-round-count).

Every view that names how many review rounds a gate has had must count them here, from
the same fold: the gate's recorded runs (`Item.gate_history`), never the current
`GateRecord`'s evidence. A later run replaces that evidence (a duplicate run, a hand
record, a re-claim), so a view that reads it can reset or disagree with the review
budget -- `gate status` said "1 full round" where `review triage` said "2 of 2 rounds"
for the same gate. `gate status` (its "1 full round, 2 delta rounds" line), the budget
and `review triage` ("N of cap rounds"), and `review show` all use these.
"""

from __future__ import annotations


def _runs(it, gate: str) -> list:
    """The gate's recorded outcomes on ``it`` (an Item or None), oldest first: its
    `gate_history`, folded once, in place of a scan of the log for each question."""
    return it.runs_of(gate) if it is not None else []


def kept_copy(run) -> bool:
    """A record `api.review._keep_reviewed_round` re-emitted over an outage: a copy of an
    earlier round, no round of its own (B435bbddb18). Copies written before `round_kept`
    existed are told by `unavailable_deltas`, which only that path writes. Both are
    MARKER tests, presence not truthiness: an empty list is still the marker."""
    return "round_kept" in run.evidence or "unavailable_deltas" in run.evidence


def settled_pass(run) -> bool:
    """A pass `api.review._settle_after_cap` recorded: it carries the settled review's
    evidence, `review_kind` included, but is no review round (roborev on 1e5dab6e)."""
    return bool(run.evidence.get("passed_on_refutation"))


def full_rounds(it, gate: str) -> int:
    """Full review rounds already recorded for ``it``'s ``gate``.

    Counted from the gate's HISTORY, not its current record: a later delta, a manual
    `gate skip`/`record` or a re-claim replaces that record's evidence, and the budget
    must not reset with it. Only rounds `ddflow review` tagged `review_kind = "full"`
    count: a refused round, an errored one, an interrupted one that recorded nothing
    and a `--chunk` re-run are not. A run whose record names a verdict it did not
    reach (`status` UNAVAILABLE/ERROR) charged a round it never delivered; a dead
    run's record says `unavailable`, so a full-shaped run recorded unavailable
    charges nothing (Bfdcb5c177c). One whose record carries NO `status` at all and
    some outcome is a declared round (a full record of the era before `status`
    evidence carried into a re-claim or hand record): it still counts, or a
    re-record would silently hand the budget back. The same era's refused and
    errored full records have no verdict either and this contract excludes them
    there too (rubber_duck finding #1): outcomes without a verdict charge nothing.
    """
    return sum(
        1
        for r in _runs(it, gate)
        if r.evidence.get("review_kind") == "full"
        and (
            r.evidence.get("status") in ("REVIEWED", "PARTIAL")
            or (
                r.evidence.get("status") is None
                and r.outcome not in (None, "unavailable", "error", "refused")
            )
        )
        and not kept_copy(r)
        and not settled_pass(r)
        and not r.evidence.get("fix_round")
        and not r.evidence.get("retry_of_partial")
    )


def delta_rounds(it, gate: str) -> int:
    """Delta rechecks already recorded for ``it``'s ``gate`` (counted from its history,
    like `full_rounds`): only ones that reached a reviewer and are a round of their own
    (a free retry of a partial round is no round, exactly as for full rounds)."""
    return sum(
        1
        for r in _runs(it, gate)
        if r.evidence.get("review_kind") == "delta"
        and r.evidence.get("status") in ("REVIEWED", "PARTIAL")
        and not kept_copy(r)
        and not settled_pass(r)
        and not r.evidence.get("fix_round")
        and not r.evidence.get("retry_of_partial")
    )


def rounds_used(it, gate: str) -> int:
    """Every review round of ``gate`` the budget counts: full and delta alike."""
    return full_rounds(it, gate) + delta_rounds(it, gate)
