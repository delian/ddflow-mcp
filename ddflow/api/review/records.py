"""Round and record reading, and the outcome a review records for one gate."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...services import gates as GD
from ...services import leasekeep as LK
from ...services import logblobs as LB
from ...services import review as RV
from ...services.gates import measured as GR
from ..gates import item_tree

PROGRESS_EVERY_S = 60
_HEARD = (RV.REVIEWED, RV.PARTIAL)  # a reviewer that finished at least part of the diff


def _lease_ticker(log, cfg, it, tick_s: float) -> Callable[[], None] | None:
    """A tick renewing the caller's lease on `it` every `lease.heartbeat_s`, or None.

    A review on a reasoning model runs 20-40 minutes against a 30-minute lease, and the
    caller is blocked in it: the lease expired mid-review, the agent's next commit was
    refused, and `next` offered the item's files to another agent while its owner was
    about to merge (bugs Bc6ec4fd40d, Bf0cccb8fb1). `gate run` already renewed; this is
    the same keeper (`gates._lease_keeper`: only the HOLDER's lease, never a
    bystander's), throttled because the review ticks more often than it must renew:
    it renews at the last tick before `heartbeat_s` has passed since the lease's own
    last renewal, so two renewals are never further apart than `heartbeat_s` (critic:
    a 0.9 x heartbeat threshold let a 60 s tick land a renewal up to a tick late).
    """

    return LK.ticker(log, cfg, it, tick_s)


def _say_result(say: Callable[[str], None], res) -> None:
    """One reviewer's verdict, then each finding with its detail."""
    say(
        f"  {res.reviewer} {res.label}: {len(res.findings)} finding(s), {res.coverage()}, "
        f"{res.elapsed_s:.1f}s" + (f" — {res.reason}" if res.reason else "")
    )
    for n, f in enumerate(res.findings, 1):
        say(f"\n  #{n} [{f.severity}] {f.location or f.title}")
        for line in f.detail.splitlines():
            if line.strip():
                say(f"      {line.strip()}")


def _prior_evidence(rec) -> dict[str, Any]:
    """The evidence of a recorded gate with its out-of-line values read back."""
    return dict(LB.full(rec.evidence)) if rec and rec.evidence else {}


def _record(
    repo, log, cfg, st, item: str, gate: str, outcome: str, called_from, gates, **kw
) -> None:
    """A review's outcome through the one measured record path: ddflow's own measurement of
    the item's tree beside what the reviewer said. An id no item has is recorded unmeasured,
    as it always was; a caller standing in another item's tree is not measured either."""
    it = st.items.get(item)
    if it is None:
        GD.record(log, cfg, item, gate, outcome, gates=gates, **kw)
        return
    wt, _held = item_tree(repo, cfg, st, it, called_from)
    GR.record_measured(log, cfg, repo, it, gate, outcome, gates=gates, tree=wt, **kw)


def _round_evidence(head, kind, done, status, forced, deltas=0) -> dict[str, Any]:
    """The round bookkeeping a recorded review carries: kind, the running count of full
    rounds and of delta rounds (kept apart: `gate status` shows both), the head it
    covered (what a delta diffs from), and a forced round's reason. A round counts only
    when a reviewer actually reviewed something.

    `head` is the one captured BEFORE the diff was taken (B4f5be8179f): a review runs for
    minutes, and the branch's head when it ends may hold commits made meanwhile that no
    reviewer saw -- recording that one let the next delta skip them."""

    reviewed = status in _HEARD
    counted = kind == "full" and reviewed
    ev: dict[str, Any] = {
        "review_kind": kind if counted or kind != "full" else "full_unavailable",
        "rounds": done + (1 if counted else 0),
        "delta_rounds": deltas + (1 if kind == "delta" and reviewed else 0),
    }
    if reviewed:
        # Only a review that reached a reviewer covered this head: recording it for one
        # that did not would let the next delta start past commits nobody reviewed.
        ev["reviewed_head"] = head
    if counted:
        ev["round"] = done + 1
    if forced:
        ev["budget_forced"] = forced
    return ev


def _review_runs(it, gate: str) -> list:
    """The gate's recorded outcomes on ``it`` (an Item or None), oldest first: its
    `gate_history`, folded once, in place of a scan of the log for each question."""
    return it.runs_of(gate) if it is not None else []


def _reviewer_families_of(results) -> list[str]:
    """The distinct families of every reviewer that came back REVIEWED, in a stable
    order. A gate dispatched to several reviewers records ONE of them (its identifying
    pass) as ``reviewer``/``family``, so a single record hid a clean cross-family panel —
    which is why reviewer independence could not see that grok AND gemini reviewed when
    the author's family is unknown (bug B1a30dc3303)."""
    return sorted(
        f
        for f in {
            (r.family or "").strip().lower() for r in (results or []) if r.status == RV.REVIEWED
        }
        if f
    )


def _reviewed_findings(results) -> list[dict[str, Any]]:
    """The `chunk_findings` of every reviewer that was heard, in venue order. The gate blocks
    on a finding from ANY of them (`_gate_outcome`), so the record keeps them all: recording
    ``best``'s alone left a failed round whose finding count named a finding the record
    did not show (bug B0419c114ef). A PARTIAL reviewer was heard too: what its finished
    chunks raised is real, and leaving it out let the `--chunk` re-run of the lost chunk
    pass over it (bug B-main-red-review-records). Each reviewer's `evidence()` builds the
    dicts with the detail truncation `_merge_delta` and triage already read."""
    return [
        fd for r in (results or []) if r.status in _HEARD for fd in r.evidence()["chunk_findings"]
    ]


def _reason_of(best, found=None) -> str:
    """The `failed` reason a round records for the gate: what the reviewer said, else the
    count of the findings the record keeps. ``found`` is the reviewed findings the record
    already built (every reviewed reviewer's, serialized to chunk dicts); a call that
    omits it falls back to ``best.findings`` so the reason never goes empty on a failed
    round (bug B0419c114ef: a delta round over a multi-reviewer gate crashed recording
    `failed` with no reason, so the round never persisted and the gate stayed failed with
    unresolved findings)."""
    if best.reason:
        return best.reason
    found = best.findings if found is None else found
    return f"{len(found)} finding(s)" if found else ""


def _finding_rows(best) -> list[dict[str, str]]:
    return [
        {"severity": f.severity, "title": f.title, "location": f.location, "detail": f.detail}
        for f in best.findings
    ]


def _item_intent(it) -> str:
    return f"{it.title}. {it.body}".strip() if it else ""


def _last_review(it, gate: str) -> dict:
    """The evidence of the gate's latest `ddflow review`, read from its history (the head
    and its status): a re-claim or `gate skip` replaces the gate's current record."""
    for r in reversed(_review_runs(it, gate)):
        if r.evidence.get("reviewed_head"):
            return LB.full(r.evidence)
    return {}


def _still_standing(it, gate: str, findings) -> set[str]:
    """The digests of a previous review's findings still standing (not refuted): a later full
    re-review raising the SAME finding again keeps blocking even below the convergence floor
    -- the floor filters only NEW below-floor findings (bug B-floor-passes-repeated-untriaged-
    nit).

    ``findings`` is a prior review's recorded ``chunk_findings`` (already the merged record:
    a delta round keeps the earlier full round's findings alongside its own), so every
    finding the author has already seen is here."""
    if it is None or not isinstance(findings, list):
        return set()
    mine = (getattr(it, "triage", None) or {}).get(gate, {})
    return {
        str(f.get("digest"))
        for f in findings
        if f.get("digest") and mine.get(f.get("digest"), {}).get("verdict") != "refuted"
    }


def _confirmed_unrechecked(it, gate: str) -> bool:
    """Is there a CONFIRMED finding of ``gate`` that no later delta round re-checked?

    A full round that merely does not raise one has not re-checked the fix, so a converged
    full re-review must not pass while one stands (bug B-push-failed-review-delta). Read from
    the recorded gate (whose latest outcome is still the previous round's `failed`) so the
    exact "confirmed and not cleared after a delta" rule lives in one place. Returns False
    for a non-item `it` (the `_gate_outcome` unit tests pass a bare object and mock
    `_last_review` instead)."""
    if it is None or not callable(getattr(it, "gate_outcome", None)):
        return False
    return bool(GD.unresolved_review_failure(it, gate, confirmed_only=True))


def _gate_outcome(
    best, results, it, item: str, gate: str, converging: bool, cfg, extra_evidence, say
) -> tuple[str, dict | None]:
    """(outcome, extra_evidence) -- the review's outcome and the (possibly updated) extra
    evidence; a converged full re-review passes with a `converged` flag on the evidence.

    The convergence floor (bug Bf797745b5a) applies to a FULL re-review only: a delta or
    fix round shows only NEW code, so its own finding is a real defect of the change and
    blocks at any severity -- while a full re-review re-sends the whole diff the author
    has already seen, so a new below-floor nit does not re-open the gate.
    """
    if best.status != RV.REVIEWED:
        return (
            {RV.PARTIAL: "partial", RV.UNAVAILABLE: "unavailable", RV.ERROR: "unavailable"}[
                best.status
            ],
            extra_evidence,
        )
    # Only after a REVIEWED round left findings to converge from: an unavailable or findingless
    # earlier round (which still spends a round) is not a previous review.
    prior = _last_review(it, gate) if it is not None and bool(item) else {}
    converged_round = converging and bool(prior.get("chunk_findings"))
    # Every heard reviewer's findings count: a below-floor nit (or no finding) from the
    # status-minimal one must not hide a HIGH one from another reviewer.
    found = [f for r in results if r.status in _HEARD for f in r.findings]
    if not found:
        return "passed", extra_evidence
    blocking = RV.blocking_findings(
        found,
        converged=converged_round,
        floor=cfg.review.converge_floor,
        known=_still_standing(it, gate, prior.get("chunk_findings")),
    )
    if blocking:
        return "failed", extra_evidence
    # A full re-review must not converge (pass on below-floor findings) while a confirmed
    # finding the gate has ever recorded still awaits a later delta round (bug
    # B-push-failed-review-delta).
    if converged_round and _confirmed_unrechecked(it, gate):
        return "failed", extra_evidence
    converged = {
        "findings_below_floor": len(found),
        "floor": cfg.review.converge_floor,
    }
    say(
        f"→ {converged['findings_below_floor']} finding(s) below [review].converge_floor "
        f"({converged['floor']}) recorded but not blocking: the full re-review passes (converged)"
    )
    return "passed", {**(extra_evidence or {}), "converged": converged}
