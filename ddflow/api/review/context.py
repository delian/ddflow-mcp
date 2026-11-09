"""The re-review prompt: convergence floor, previous findings, and injected guidance."""

from __future__ import annotations

from collections.abc import Callable

from ...core import blobref as B
from ...services.gates.failed_review import _cleared_after_delta
from ...services.guidance import inject as GI
from .records import _last_review

_BACKGROUND_HINT = (
    "a review can take minutes: a harness that cuts a foreground command (often at 120 s) "
    "kills it -- run `ddflow review --detach`, which reviews in its own session and streams "
    "to a log it names, so a cut reaches only the launcher and the review outlives it "
    "(a plain run in the background dies with the cut)."
)


_PREVIOUS_DETAIL = 500


_PREVIOUS_PROBE = 300


_DELTA_CAVEAT = (
    "You are shown only the commits since the reviewed head, not the whole change: an "
    "earlier fix may sit in code you do not see. A `confirmed` finding is the author's "
    "claim, not proof: report it if these commits show it still holds or contradict the "
    "claim; where the delta alone cannot tell, say it cannot be verified from a delta "
    "instead of calling it fixed or unfixed."
)


def _converges(kind: str, fix: bool) -> bool:
    """Whether the convergence floor applies: a full or delta, non-fix re-review.

    A delta round shows only the new commits, but the author has still triaged every
    finding the earlier rounds recorded, so a delta that keeps raising a new MEDIUM or
    LOW nit must converge the same way a full re-review does (bug
    B-review-delta-rounds-do-not-converge). A fix round re-checks the author's fixes
    alone, where any new finding is a real defect of the new code, so it still blocks at
    any severity.
    """
    return kind in ("full", "delta") and not fix


def _floor_for(cfg, kind: str, fix: bool) -> str:
    """The floor the re-review prompt names, "" where it does not apply."""
    return cfg.review.converge_floor if _converges(kind, fix) else ""


def _floor_lines(floor: str) -> list[str]:
    """The prompt's convergence-floor paragraph; empty when no floor applies (a chunk
    re-run or a fix round, where every finding blocks, bug Bf797745b5a)."""
    if not floor:
        return []
    return [
        f"Do not report a NEW finding below {floor} severity against a file that is unchanged "
        "since the last review, or against a line outside the hunks this change adds or edits: "
        "where the code stands exactly as the earlier round saw it, a new objection to it is a "
        "re-read of the same code, not a new defect of this change. Reserve new findings at "
        f"or above {floor} severity for the newly changed lines.",
        "",
    ]


def _with_previous_findings(  # noqa: PLR0913 -- the previous round plus the floor
    context: str,
    log,
    it,
    item: str,
    gate: str,
    say,
    *,
    kind: str,
    rerun: dict,
    supers: set[str] = frozenset(),
    fix: bool = False,
    floor: str = "",
) -> str:
    """``context`` plus the gate's previous findings and the author's triage of each, for a
    full re-review (decision D-gate-economy 3) and for a delta (B64ed3cae18: shown only the
    follow-up, the reviewer contradicted the earlier fix it could not see).

    The reviewer sees the item's whole diff AND what was said about it last time: each
    finding with `confirmed` (the author says it is fixed: check the fix), `refuted` (the
    author's probe shows it false: check the probe) or `untriaged`. Without them a
    re-review either re-reports what was settled or, as a delta of only the follow-up
    commit, reports the fix as absent. Nothing to add for a first review or a `--chunk`
    re-run (``rerun``).
    """
    if (kind not in ("full", "delta") and not fix) or rerun or it is None:
        return context
    found = _last_review(it, gate).get("chunk_findings")
    if not found or isinstance(found, B.Unavailable):
        return context
    mine = it.triage.get(gate, {})
    cleared = _cleared_confirmed(it, gate) if kind == "delta" else set()
    look = "the commits shown" if kind == "delta" else "the whole diff"
    lines = [
        "## Previous findings on this change, and the author's triage",
        "",
        *([_DELTA_CAVEAT, ""] if kind == "delta" and not fix else []),
        "An earlier review of this item reported the findings below. For each: if the "
        "diff now answers it (a `confirmed` one should be fixed; a `refuted` one should stay "
        "refuted by its probe), do not report it again; report it only if it still holds, "
        "saying why the fix or the probe does not answer it. Do not restate a finding from "
        "memory: check each previous finding against the current code, and do not carry it over "
        "when the code it cites is gone or the change answers it; if the same defect "
        "still stands (even moved or edited), report it at its new place. Then look for NEW "
        f"issues in {look} -- these are not the only places to look.",
        "",
        *_floor_lines(floor),
    ]
    shown = 0
    for f in found:
        if str(f.get("digest") or "") in cleared:
            continue
        shown += 1
        lines.extend(_finding_lines(shown, f, mine, supers))
    if shown == 0:
        return context
    scope = (
        f"the delta of {item} (commits since the reviewed head)"
        if kind == "delta"
        else f"the whole diff of {item}"
    )
    say(
        f"re-review: {scope}, with {shown} previous {gate} finding(s) "
        "and their triage (D-gate-economy)"
    )
    return "\n\n".join(x for x in (context.strip(), "\n".join(lines)) if x)


def _cleared_confirmed(it, gate: str) -> set[str]:
    """Digests of ``gate``'s CONFIRMED findings a later delta round already re-checked and
    did not raise again. Their fix sits before the next delta's base, so that delta cannot
    see it and must not re-send the finding (bug B2e1f4aedb5: the reviewer reported one
    again as unfixed because the named probe was not in the diff)."""
    runs = it.runs_of(gate)
    return {
        str(d)
        for d, t in (it.triage.get(gate, {})).items()
        if t.get("verdict") == "confirmed" and _cleared_after_delta(runs, str(d))
    }


def _finding_lines(n: int, f: dict, mine: dict, supers) -> list[str]:
    """One previous finding for the prompt: what it said, then the author's triage of it
    (or that its lines were rewritten since)."""
    t = mine.get(str(f.get("digest") or ""), {})
    verdict = t.get("verdict") or "untriaged"
    detail = " ".join(str(f.get("detail") or f.get("title") or "").split())
    probe = " ".join(str(t.get("probe") or "").split())
    head = (
        f"{n}. [{f.get('severity', '')}] {f.get('location') or f.get('title', '')}: "
        f"{detail[:_PREVIOUS_DETAIL]}" + (" ..." if len(detail) > _PREVIOUS_DETAIL else "")
    )
    if f.get("digest") in supers:
        return [
            head,
            "   SUPERSEDED: the lines it cites were rewritten since the reviewed commit; "
            "do NOT report this finding again; report only a NEW defect of the new code",
        ]
    return [
        head,
        f"   triage: {verdict}" + (f" -- probe: {probe[:_PREVIOUS_PROBE]}" if probe else ""),
    ]


def _with_guidance(context: str, cfg, st, item: str, gate: str, say: Callable[[str], None]) -> str:
    """``context`` with the project's guidance that governs the item at this gate, fenced as
    data, and the reviewer told to verify the change against it and cite the id of one it
    violates (B-uni-guidance-inject). Unchanged when nothing governs the item."""
    found = GI.for_item(
        cfg,
        st,
        item,
        gate=gate,
        budget=GI.door_budget(cfg),
        verify=True,
        heading="## Project guidance to check this change against",
    )
    if not found.text:
        return context
    say(f"→ {len(found.shown)} piece(s) of guidance sent with the diff: {', '.join(found.cited)}")
    return f"{context}\n\n{found.text}".strip() if context else found.text.strip()
