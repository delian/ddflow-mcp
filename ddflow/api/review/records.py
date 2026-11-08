"""Round and record reading, and the outcome a review records for one gate."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core import blobref as B
from ...services import gates as GD
from ...services import leasekeep as LK
from ...services import logblobs as LB
from ...services import review as RV
from ...services.gates import measured as GR
from ...services.gates.failed_review import _cleared_after_delta
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


def _say_result(say: Callable[[str], None], res, kept=None) -> None:
    """One reviewer's verdict, then each finding with its detail.

    ``kept`` is the gate's earlier recorded findings (a delta's): a finding is numbered by
    its place in the RECORD -- the list `review triage --finding N` addresses -- not by this
    round alone, so a delta's new finding keeps the number #5 it has on the record instead
    of renumbering to #1 and making a triage mark a different finding (bug B32ce00713b).
    """
    say(
        f"  {res.reviewer} {res.label}: {len(res.findings)} finding(s), {res.coverage()}, "
        f"{res.elapsed_s:.1f}s" + (f" — {res.reason}" if res.reason else "")
    )
    for n, f in _record_numbers(res, kept):
        say(f"\n  #{n} [{f.severity}] {f.location or f.title}")
        for line in f.detail.splitlines():
            if line.strip():
                say(f"      {line.strip()}")


def _record_numbers(res, kept):
    """(record number, finding) pairs: a round's findings numbered #1..#K as a full round
    numbers them, or each delta finding numbered where its digest stands in ``kept`` (the
    earlier findings a byte-identical one stays bound to; a new one is the next place).

    ``kept`` is the caller's growing list of the findings seen before, extended here as
    each finding is emitted, so a second reviewer's delta finding is numbered after the
    first's (bug B32ce00713b, raised two reviewers a delta round numbers the same)."""
    if kept is None:
        return list(enumerate(res.findings, 1))
    have = {str(f.get("digest") or ""): n for n, f in enumerate(kept, 1)}
    numbered: list[tuple[int, Any]] = []
    for f in res.findings:
        digest = str(getattr(f, "digest", "") or "")
        if digest and digest in have:
            n = have[digest]
        else:
            kept.append({"digest": digest})
            n = len(kept)
            if digest:
                have[digest] = n
        numbered.append((n, f))
    return numbered


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
    dicts with the detail truncation `_merge_delta` and triage already read.

    Each finding carries its reviewer's ``reviewer`` and ``family``: the same-family
    discount of bug B7dbf432caa reads a confirmed finding's family from the record."""
    out: list[dict[str, Any]] = []
    for r in results or []:
        if r.status not in _HEARD:
            continue
        who = getattr(r, "reviewer", "")
        fam = (getattr(r, "family", "") or "").strip().lower()
        for fd in r.evidence()["chunk_findings"]:
            out.append({**fd, "reviewer": who, "family": fam})
    return out


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


def _finding_rows(results) -> list[dict[str, str]]:
    """The review's returned findings: every reviewer the run heard, not only the one it
    names, each row attributed to the reviewer that raised it. The gate record already
    keeps every heard reviewer's findings (`_reviewed_findings`, bug B0419c114ef), but the
    caller's ``data["findings"]`` named a single reviewer's alone, so a clean identifying
    reviewer hid a finding a secondary reviewer raised -- the review reported no finding
    while the record blocked on one (bug B-review-drops-a-reviewers-finding)."""
    rows: list[dict[str, str]] = []
    for r in results or []:
        if r.status not in _HEARD:
            continue
        rows.extend(
            {
                "severity": f.severity,
                "title": f.title,
                "location": f.location,
                "detail": f.detail,
                "reviewer": r.reviewer,
                "family": r.family,
            }
            for f in r.findings
        )
    return rows


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


def _confirmed_unrechecked(it, gate: str, results, cfg, author_model: str = "") -> bool:
    """Is there a CONFIRMED finding of ``gate`` that no later delta round re-checked, that
    still blocks?

    A full round that merely does not raise one has not re-checked the fix, so a converged
    full re-review must not pass while one stands (bug B-push-failed-review-delta). Read from
    the recorded gate (whose latest outcome is still the previous round's `failed`) so the
    exact "confirmed and not cleared after a delta" rule lives in one place. Returns False
    for a non-item `it` (the `_gate_outcome` unit tests pass a bare object and mock
    `_last_review` instead).

    A confirmed finding raised by a reviewer of the AUTHOR's own family does not block when
    a cross-family reviewer came back clean: it is not independent evidence, so it is
    discounted the same way this round's same-family findings are (bug B7dbf432caa). A
    confirmed finding from another family still blocks.
    """
    if it is None or not callable(getattr(it, "gate_outcome", None)):
        return False
    if it.gate_outcome(gate) != "failed":
        return False
    unresolved, unreadable = _unrechecked_confirmed(it, gate)
    if unreadable:
        return True  # fail closed: a confirmed finding that cannot be read back still blocks
    if not unresolved:
        return False
    author_fam = _author_single_family(cfg, author_model)
    heard = [r for r in (results or []) if getattr(r, "status", None) in _HEARD]
    if author_fam and _cross_family_clean(heard, cfg, author_fam):
        # Discount ONLY a same-family confirmed finding over a clean cross-family verdict.
        # An empty family is not the author family: it cannot be told, and it may well be a
        # cross-family reviewer recorded before the finding carried a family, so it still
        # blocks (lan-deepseek/grok re-review of bug B7dbf432caa).
        return any(fam != author_fam for _d, fam in unresolved)
    return True


def _unrechecked_confirmed(it, gate: str) -> tuple[list[tuple[str, str]], bool]:
    """((digest, family) of every CONFIRMED finding of ``gate`` no later delta round
    cleared, read from the recorded findings, and whether any could not be read back).

    Each finding carries the raising reviewer's ``family`` (since bug B7dbf432caa); a
    finding read from a run recorded before that stamp has no ``family`` key and yields an
    empty family, so its family really is unknown."""
    mine = (getattr(it, "triage", None) or {}).get(gate, {})
    runs = it.runs_of(gate)
    unreadable = False
    explicit: dict[str, str] = {}
    fallback: dict[str, str] = {}
    unconfirmed: set[str] = set()
    for run in runs:
        run_fam = _normalise_family((run.evidence or {}).get("family"))
        found = LB.full(run.evidence or {}).get("chunk_findings")
        if isinstance(found, B.Unavailable):
            unreadable = True  # "cannot be read back" is not "no confirmed finding"
            continue
        for f in found if GD.is_triageable(found) else []:
            digest = f.get("digest")
            if not digest:
                continue
            stamp = _normalise_family(f.get("family"))
            # An explicit per-finding stamp is the finding's true raiser and always outranks
            # a run-level fallback (the run's identifying family is only a guess); the first
            # explicit stamp is its origin. The fallback fills a digest nobody stamped, and
            # the guards keep an empty value out of both maps.
            if stamp:
                explicit.setdefault(digest, stamp)
            fam = stamp or run_fam
            if fam:
                fallback.setdefault(digest, fam)
            if mine.get(digest, {}).get("verdict") == "confirmed" and not _cleared_after_delta(
                runs, digest
            ):
                unconfirmed.add(digest)
    fam_by_digest = {d: explicit.get(d) or fallback.get(d, "") for d in unconfirmed}
    return [(d, fam_by_digest.get(d, "")) for d in unconfirmed], unreadable


def _normalise_family(value) -> str:
    """A family value from a finding or a run, lowercased, with the literal ``unknown``
    (and an empty/None value) normalised to "". A name the map cannot tell returns ""."""
    fam = (str(value or "") or "").strip().lower()
    return "" if fam == "unknown" else fam


def _author_single_family(cfg, author_model: str) -> str | None:
    """The AUTHOR's one family for the same-family discount, or None when none can be
    named: an empty author model, a name no map recognises, or a router author (a SET of
    families has no single line a reviewer can be shown on or off). Mirrors
    `gates._author_identity` over its exported pieces."""
    if not author_model:
        return None
    bare = GD.family_name_of(author_model, cfg.agent.families)
    routed = None if bare else GD.router_set(author_model, cfg.agent.routers)
    if routed is not None:
        return None
    fam = (bare or GD.family_of(author_model, cfg) or "").strip().lower()
    return None if fam in ("", "unknown") else fam


def _rev_family(r, cfg) -> str:
    """A reviewer result's resolved family, lowercased, ``unknown`` normalised to "" --
    after the fallback too, so a family that the map itself names ``unknown`` is still
    "cannot be told", never an independent cross family."""
    fam = (getattr(r, "family", "") or "").strip().lower()
    fam = "" if fam == "unknown" else fam
    if not fam:
        fam = (GD.family_of(getattr(r, "model", "") or "", cfg) or "").strip().lower()
        fam = "" if fam == "unknown" else fam
    return fam


def _cross_family_clean(results, cfg, author_fam: str | None) -> bool:
    """Is there a cross-family clean verdict: a reviewer whose family lies outside the
    author's that came back REVIEWED (a full round) with no findings?"""
    if not author_fam:
        return False
    return any(
        getattr(r, "status", None) == RV.REVIEWED
        and not getattr(r, "findings", None)
        and _rev_family(r, cfg) != author_fam
        and _rev_family(r, cfg) != ""
        for r in (results or [])
    )


def _blocking_findings(
    results, it, gate: str, converging: bool, cfg, author_model: str = ""
) -> tuple[list, list, bool]:
    """(blocking, discounted, converged_round) for this round -- the findings that keep the
    gate open, the findings a same-family/unrecognized reviewer raised that do NOT, and
    whether this is a converged full re-review; computed in ONE place so the caller can name
    the reviewer that found a blocking finding and `_gate_outcome` shares this round's
    ``converged_round`` with it (bug B3113b1f522: one prior/converged value, not two drifted
    copies). ``converged_round`` is False when this round raised no finding.

    ``discounted`` is the rubric of bug B7dbf432caa: a finding raised only by a reviewer of
    the author's OWN family (or one whose family cannot be told, which ``review_tier`` calls
    "establishes nothing") is not independent evidence, so when a cross-family reviewer came
    back clean (``_cross_family_clean``) those findings are recorded but do not hold the
    pass open -- otherwise the author's own family would veto a clean cross-family review
    forever by never converging to a clean delta round."""
    heard = [r for r in results if getattr(r, "status", None) in _HEARD]
    author_fam = _author_single_family(cfg, author_model)
    cross_clean = _cross_family_clean(heard, cfg, author_fam)
    found: list = []
    discounted: list = []
    for r in heard:
        fs = list(getattr(r, "findings", []) or [])
        if not fs:
            continue
        is_cross = bool(_rev_family(r, cfg)) and _rev_family(r, cfg) != author_fam
        if cross_clean and not is_cross:
            discounted.extend(fs)
        else:
            found.extend(fs)
    if not found and not discounted:
        return [], [], False
    prior = _last_review(it, gate) if it is not None else {}
    converged_round = converging and bool(prior.get("chunk_findings"))
    blocking = RV.blocking_findings(
        found,
        converged=converged_round,
        floor=cfg.review.converge_floor,
        known=_still_standing(it, gate, prior.get("chunk_findings")),
    )
    return blocking, discounted, converged_round


def _gate_outcome(
    best,
    results,
    it,
    item: str,
    gate: str,
    converging: bool,
    cfg,
    extra_evidence,
    say,
    author_model: str = "",
) -> tuple[str, dict | None]:
    """(outcome, extra_evidence) -- the review's outcome and the (possibly updated) extra
    evidence; a converged full re-review passes with a `converged` flag on the evidence, and
    a round whose only findings are a same-family reviewer's (over a clean cross-family
    verdict) passes with a `same_family_discounted` flag (bug B7dbf432caa).

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
    # Every heard reviewer's findings count: a below-floor nit (or no finding) from the
    # status-minimal one must not hide a HIGH one from another reviewer.
    found = [f for r in results if r.status in _HEARD for f in r.findings]
    if not found:
        return "passed", extra_evidence
    blocking, discounted, converged_round = _blocking_findings(
        results, it, gate, converging, cfg, author_model
    )
    if blocking:
        return "failed", extra_evidence
    # A full re-review must not converge (pass on below-floor findings) while a confirmed
    # finding the gate has ever recorded still awaits a later delta round (bug
    # B-push-failed-review-delta). A same-family confirmed finding is discounted over a
    # clean cross-family verdict inside `_confirmed_unrechecked` (bug B7dbf432caa).
    if converged_round and _confirmed_unrechecked(it, gate, results, cfg, author_model):
        return "failed", extra_evidence
    # A same-family reviewer's finding does not hold the pass open when a cross-family
    # reviewer came back clean: cross-family clean is authoritative (bug B7dbf432caa). The
    # discounted findings stay on the record, just not blocking -- so the author's own
    # family cannot veto a clean independent review by never converging.
    if discounted:
        extra = {
            **(extra_evidence or {}),
            "same_family_discounted": {
                "findings": len(discounted),
                "note": "a cross-family reviewer passed clean",
            },
        }
        say(
            f"→ {len(discounted)} finding(s) from a same-family/unrecognized reviewer "
            "recorded but not blocking: a cross-family reviewer passed clean"
        )
        return "passed", extra
    converged = {
        "findings_below_floor": len(found),
        "floor": cfg.review.converge_floor,
    }
    say(
        f"→ {converged['findings_below_floor']} finding(s) below [review].converge_floor "
        f"({converged['floor']}) recorded but not blocking: the full re-review passes (converged)"
    )
    return "passed", {**(extra_evidence or {}), "converged": converged}
