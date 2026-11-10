"""Findings bookkeeping: carry triage verdicts, merge a delta's findings into a record, link refuted claims."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ...core import blobref as B
from ...services import gates as GD
from ...services import logblobs as LB
from ...services import review as RV
from ..review_supersede import carry
from .delta import _last_head
from .records import _prior_evidence


def _finding_listing(it, gate: str) -> str:
    """The numbered findings a triage can address, one per line with their verdict, for the
    refusal that asks which verdict (bug B29667ba4b4); "" when none can be read."""
    gates = (
        [gate]
        if gate
        else sorted(g for g, r in it.gates.items() if (r.evidence or {}).get("chunk_findings"))
    )
    lines = []
    for g in gates:
        rec = it.gates.get(g)
        found = LB.full(rec.evidence or {}).get("chunk_findings") if rec else None
        if not GD.is_triageable(found):
            continue
        mine = it.triage.get(g, {})
        lines += [
            f"  {g} #{n} [{f.get('severity', '')}] {f.get('location', '')} "
            f"{f.get('title', '')} -- "
            f"{mine.get(f['digest'], {}).get('verdict') or ('malformed' if f.get('malformed') else 'untriaged')}"
            f"{(' (' + f['malformed'] + ')') if f.get('malformed') else ''}"
            for n, f in enumerate(found, 1)
        ]
    return "\nfindings:\n" + "\n".join(lines) if lines else ""


def _chunk_numbers(value) -> list[int] | str:
    """`--chunk 2 --chunk 5`, `--chunk 2,5`, or an MCP list of numbers: sorted, once each."""
    items = [value] if isinstance(value, (str, int)) else list(value or [])
    out: set[int] = set()
    for item in items:
        for part in str(item).replace(";", ",").split(","):
            if not part.strip():
                continue
            try:
                out.add(int(part))
            except ValueError:
                return f"--chunk takes chunk numbers, e.g. 5 or 2,5; got {part.strip()!r}"
    return sorted(out) or "--chunk names no chunk"


def _announce_rerun(rerun, revs, item: str, gate: str, say):
    """(reviewers, the record to merge into, the chunks) -- `--chunk`'s plan, said aloud;
    for any other review, (all of them, nothing, every chunk)."""
    if not rerun:
        return revs, {}, None
    revs, prior, only = rerun
    say(f"→ re-reviewing chunk(s) {only} of {item}.{gate} (recorded: {prior['coverage']})")
    return revs, prior, only


def _hold_unreadable(it, gate: str, lost, ev: dict[str, Any], say) -> int:
    """Earlier findings that cannot be read back are earlier findings nobody has settled:
    never merged as if there were none, and the gate is held (`_hold`)."""
    ev["prior_findings_unavailable"] = str(lost)
    say(f"the earlier findings of {it.id}.{gate} are {lost}: this delta cannot clear the gate.")
    return 1


def _digests(found: list[dict]) -> list[str]:
    return [str(f["digest"]) for f in found if f.get("digest")]


def _finding_field(f, key: str) -> str:
    """``key`` of a finding, however it is carried: a ``Finding`` or its evidence dict."""
    return f.get(key, "") if isinstance(f, dict) else (getattr(f, key, "") or "")


def _link_refuted(log, it, gate: str, findings) -> list[str]:
    """Auto-link this round's findings byte-identical to one the item already REFUTED on any
    gate to that triage (bug B74d5c39e6e): one claim is refuted once, and a second gate
    raising it word-for-word must not ask the author to record the same refutation again.

    Appends the item's refuted triage -- the verdict and its probe -- to ``gate`` for each
    such digest ``gate`` has no verdict on. Returns the digests linked (none when ``it`` is
    unreadable, the findings cannot be read, or nothing matches).
    """
    if it is None or not isinstance(findings, list):
        return []
    refuted = {
        digest: t
        for triaged in it.triage.values()
        for digest, t in triaged.items()
        if t.get("verdict") == "refuted"
    }
    mine = it.triage.get(gate, {})
    linked: list[str] = []
    for n, f in enumerate(findings, 1):
        digest = str(_finding_field(f, "digest") or "")
        if not digest or digest not in refuted or digest in mine:
            continue
        log.append(
            "review.triaged",
            it.id,
            {
                "gate": gate,
                "finding": n,
                "digest": digest,
                "verdict": "refuted",
                "probe": refuted[digest].get("probe", ""),
                "severity": _finding_field(f, "severity"),
                "title": _finding_field(f, "title"),
                "location": _finding_field(f, "location"),
            },
        )
        mine = {**mine, digest: refuted[digest]}
        linked.append(digest)
    return linked


def _match_sibling(found: list[dict], digest: str, point: str, claim: str) -> int:
    """The 1-based place in ``found`` of the finding this triage answers: the one under
    ``digest`` -- the byte-identical copy a combined review stores under every gate -- else
    one at the SAME design point with the same claim under a DIFFERENT digest (a re-worded
    or shifted-line re-raise of the same finding, bug Bbd391bfef5); 0 when neither matches.
    """
    by_digest = next((i for i, f in enumerate(found, 1) if f.get("digest") == digest), 0)
    if by_digest or not point or not claim:
        return by_digest
    return next(
        (
            i
            for i, f in enumerate(found, 1)
            if f.get("digest") != digest
            and _design_point(_finding_field(f, "location")) == point
            and _claim_text(f) == claim
        ),
        0,
    )


def _mirror_sibling_gates(
    log,
    it,
    *,
    gate: str,
    digest: str,
    verdict: str,
    probe: str,
    severity: str,
    title: str,
    location: str,
    detail: str = "",
) -> list[str]:
    """A combined review stores each finding under every gate it stands for; when a triage
    gives one gate's copy its verdict, the SAME finding under the other gates answers too
    (bug B-combined-review-findings-triaged-twice). The same finding can sit under a
    different digest per gate (its cited line shifted, its wording re-written), so a
    sibling copy that matches the finding's design point and claim carries the verdict
    under ITS OWN digest (bug Bbd391bfef5). Appends a `review.triaged` for each sibling
    gate that carries the finding with no settled verdict yet. Returns those gates.
    """
    if it is None:
        return []
    point = _design_point(location)
    claim = _claim_text({"detail": detail, "title": title})
    mirrored: list[str] = []
    for g2 in sorted(it.gates or {}):
        if g2 == gate:
            continue
        mine = it.triage.get(g2, {})
        rec = it.gates.get(g2)
        found = LB.full(rec.evidence or {}).get("chunk_findings") if rec else None
        if not GD.is_triageable(found):
            continue
        n = _match_sibling(found, digest, point, claim)
        if not n:
            continue
        sibling = found[n - 1]
        sib_digest = str(_finding_field(sibling, "digest") or "")
        if mine.get(sib_digest, {}).get("verdict", "") in _SETTLED:
            continue
        log.append(
            "review.triaged",
            it.id,
            {
                "gate": g2,
                "finding": n,
                "digest": sib_digest,
                "verdict": verdict,
                "probe": probe,
                "severity": _finding_field(sibling, "severity"),
                "title": _finding_field(sibling, "title"),
                "location": _finding_field(sibling, "location"),
            },
        )
        mirrored.append(g2)
    return mirrored


_LINE_CITATION = re.compile(r"^\d+(?:\s*[-\u2013]\s*\d+)?$")


def _design_point(location: str) -> str:
    """A finding's cited place with its churning line number dropped -- ``path:line`` ->
    ``path``, ``path:func`` kept whole -- so two rounds that cite the same file (and
    function/symbol, when one is named) describe the same place. A file-level citation with
    no colon already names just the place, and is kept whole. The line is what moves as the
    diff grows (bug B-review-delta-retriages-same-point)."""
    loc = " ".join(str(location or "").split()).strip()
    if not loc:
        return ""
    if ":" not in loc:
        return loc
    path, _, anchor = loc.rpartition(":")
    anchor = anchor.strip()
    return path.strip() if _LINE_CITATION.match(anchor) else loc


def _claim_text(f) -> str:
    """A finding's claim with its whitespace collapsed: what the finding SAYS is wrong, not
    where it cites it. Two re-worded claims are NOT the same claim, so carrying a verdict
    keyed by it can never hide a different finding at the same place. A whitespace-only
    detail falls back to the title (the finding's cited text), so a finding with no body
    still carries its claim from the citation."""
    detail = " ".join(str(_finding_field(f, "detail") or "").split())
    if detail:
        return detail
    return " ".join(str(_finding_field(f, "title") or "").split())


def _carry_delta_verdicts(log, it, gate: str, kept, mine, say) -> dict:
    """Carry the same gate's triage to a delta finding that re-raises an already-triaged
    design point under a new digest (bug B-review-delta-retriages-same-point).

    A delta shows the reviewer only the follow-up commits; the design point the author
    already triaged (refuted or confirmed) can come back at a shifted line, so its digest --
    what a triage is keyed by (decision D-review-triage) -- no longer attaches. A finding of
    ``mine`` matching an earlier finding of ``gate`` at the same design point (the cited file
    and function, `_design_point`, AND the same claim `_claim_text`) inherits that verdict
    instead of re-opening the triage cycle; a different claim at the same place stays its own
    untriaged finding. Appends a `review.triaged` for each and returns them keyed by digest.
    A byte-identical re-raise keeps its digest and so is skipped (it already stays the
    earlier triaged copy).
    """
    if it is None:
        return {}
    triage = it.triage.get(gate, {})
    prior_digests = {str(_finding_field(f, "digest") or "") for f in kept}
    if not triage or not prior_digests:
        return {}
    by_point = {}
    for f in kept:
        point = _design_point(_finding_field(f, "location"))
        claim = _claim_text(f)
        t = triage.get(str(_finding_field(f, "digest") or ""))
        if point and claim and t and t.get("verdict") in _SETTLED:
            by_point[(point, claim)] = t  # the latest settled triage for this design point
    if not by_point:
        return {}
    carried: dict = {}
    for n, f in enumerate(mine, 1):
        digest = str(_finding_field(f, "digest") or "")
        if not digest or digest in prior_digests:
            continue
        point = _design_point(_finding_field(f, "location"))
        t = by_point.get((point, _claim_text(f)))
        if not point or t is None:
            continue
        log.append(
            "review.triaged",
            it.id,
            {
                "gate": gate,
                "finding": n,
                "digest": digest,
                "verdict": t.get("verdict", ""),
                "probe": t.get("probe", ""),
                "severity": _finding_field(f, "severity"),
                "title": _finding_field(f, "title"),
                "location": _finding_field(f, "location"),
            },
        )
        carried[digest] = t
    if carried:
        say(
            f"carried the triage of {len(carried)} re-raised design point(s) to this delta's "
            "findings (their line moved or was reworded, not triaged anew)"
        )
    return carried


def _merge_delta(log, it, gate: str, kind, status, ev: dict[str, Any], say) -> int:
    """Fold a delta's findings and coverage into the evidence of the review it follows.

    The record keeps the earlier findings in place (so their numbers, and the triage
    keyed by the digest of their exact text, stay as they were) and appends the delta's
    new ones; a finding byte-identical to an earlier one is that finding, not a second
    copy. `coverage` stays the delta's (a partial one must not read as a full pass); the
    last full round's is kept as `full_coverage`. A no-op for anything but a delta that
    reached a reviewer.

    Returns how many findings of the merged record have no triage verdict yet: a clean
    delta does not clear a gate whose earlier findings nobody has settled (`_hold`).
    """

    if kind != "delta" or status not in (RV.REVIEWED, RV.PARTIAL):
        return 0
    ev["delta_from"] = _last_head(it, gate)
    rec = it.gates.get(gate) if it else None
    prior = _prior_evidence(rec)
    if isinstance(prior.get("chunk_findings"), B.Unavailable):
        return _hold_unreadable(it, gate, prior["chunk_findings"], ev, say)
    kept = list(prior.get("chunk_findings") or [])
    mine = list(ev.get("chunk_findings") or [])
    ev["delta_findings"] = len(mine)
    # Which findings THIS round raised (digests): the merged list below cannot tell, and
    # `complete` needs it to know an earlier finding was not raised again (B85841c0846).
    ev["delta_raised"] = _digests(mine)
    if prior.get("full_coverage") or prior.get("review_kind") in ("full", "full_unavailable"):
        ev["full_coverage"] = prior.get("full_coverage") or prior.get("coverage", "")
    if not kept:
        return 0
    have = {f.get("digest"): n for n, f in enumerate(kept, 1)}
    carried = _carry_delta_verdicts(log, it, gate, kept, mine, say)
    new: set[str] = set()
    for f in mine:
        if f.get("digest") not in have:
            kept.append(f)
            have[f.get("digest")] = len(kept)
            if f.get("digest"):
                new.add(f.get("digest"))
        else:  # the earlier copy stays, numbered in the earlier cut (`merge_rerun`)
            ev["delta_reraised"] = [*ev.get("delta_reraised", []), f.get("digest")]
    gone = carry(ev, prior)
    ev["chunk_findings"] = kept
    ev["findings"] = len(kept)
    ev["titles"] = [f.get("title", "") for f in kept[:10]]
    if mine:
        where = ", ".join(f"#{n} -> #{have[f.get('digest')]}" for n, f in enumerate(mine, 1))
        say(
            f"merged into the record of {it.id}.{gate} ({len(kept)} finding(s) in all): this "
            f"delta's numbers above are its own; to triage use the record's -- {where}."
        )
    verdicts = it.triage.get(gate, {})
    return _untriaged(kept, gone, carried, verdicts, new=new)


def _untriaged(
    kept: list[dict],
    gone,
    carried: dict,
    verdicts: dict,
    *,
    new: set[str] | frozenset[str] = frozenset(),
) -> int:
    """Findings of the merged record still without a settled verdict. A carried digest's
    triage is a fresh `review.triaged` `_carry_delta_verdicts` appends, not one the folded
    ``verdicts`` held, so it is excluded per finding and never over-subtracted.

    ``new`` is this delta's OWN fresh digests (not byte-identical re-raises): those do not
    hold the pass. Their blocking weight was already judged by `_gate_outcome` -- a
    below-floor new finding it passed is recorded but not blocking (bug
    B-review-delta-rounds-do-not-converge), and one at or above the floor already recorded
    the round failed -- so `_hold` counts only EARLIER findings the delta could not see.
    """
    carried_set = set(carried)
    return sum(
        1
        for f in kept
        if f.get("digest") not in gone
        and f.get("digest") not in carried_set
        and f.get("digest") not in new
        and not f.get("malformed")
        and verdicts.get(f.get("digest"), {}).get("verdict") not in _SETTLED
    )


def _replaced_by_rerun(prior: dict[str, Any], again: set[int]) -> set[str]:
    """Digests of the delta's own findings of the chunks ``again`` that a re-run replaces
    (one the delta raised again is the earlier copy, numbered in another cut: it stays)."""
    earlier = {str(d) for d in prior["delta_raised"]} - {
        str(d) for d in prior.get("delta_reraised", ())
    }
    return {
        str(f.get("digest"))
        for f in prior.get("chunk_findings") or []
        if int(f.get("chunk", 0)) in again and str(f.get("digest")) in earlier
    }


def _track_rerun_raised(evidence: dict[str, Any], prior: dict[str, Any], best) -> None:
    """A `--chunk` re-run of a delta record: the findings it raised are the delta's own, so
    `delta_raised` names them, and drops the delta's earlier findings of the chunks it
    replaced (Be7aea9436a: left out, a later re-run of that chunk kept them)."""
    if "delta_raised" not in evidence:
        return
    again = set(best.requested or ())
    replaced = _replaced_by_rerun(prior, again)
    held = {str(f.get("digest")) for f in prior.get("chunk_findings") or []}
    # a finding the merge kept from the earlier record is not this re-run's
    new = [
        f.digest
        for f in best.findings
        if f.chunk in again and f.digest and (f.digest not in held or f.digest in replaced)
    ]
    raised = [d for d in evidence["delta_raised"] if d not in replaced]
    evidence["delta_raised"] = raised + [d for d in new if d not in raised]


_SETTLED = ("refuted", "confirmed")


_DELTA_KEYS = ("delta_from", "delta_raised", "delta_reraised", "delta_findings", "full_coverage")


def _hold(outcome: str, reason: str, untriaged: int, say) -> tuple[str, str]:
    """A clean delta passes only when every earlier finding on the record has a verdict.

    Before deltas were the default a re-review read the whole diff and reported an
    unfixed finding again, so the gate stayed `failed` until it was dealt with; a delta
    does not see it, so the record's own findings are what keeps the gate honest.
    `review triage` (refuted, or confirmed with the fix as the probe) releases it.
    """
    if outcome != "passed" or not untriaged:
        return outcome, reason
    why = (
        f"the delta is clean, but {untriaged} earlier finding(s) on the record have no "
        "triage verdict: `ddflow review triage` settles each (the next clean delta then passes)"
    )
    say(f"NOTE: {why}.")
    return "failed", why


def _say_triage_scope(say: Callable[[str], None], results: list, best) -> None:
    """With several reviewers each one's findings were numbered from #1, but only the
    recorded reviewer's can be triaged: say whose (critic: another's #1 is not it)."""
    if len(results) > 1 and best.findings:
        say(f"triage addresses {best.reviewer}'s findings: #1..#{len(best.findings)}")
