"""The gate half of the completion report (`services.report`): each gate's outcome with its
reviewer, the gates recorded by hand, the evidence from an earlier tree, the overrides and what
was not done."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import gates as G
from .gates.reviewers import family_of

#: Evidence keys that show a tool or a reviewer produced the outcome. A gate with none of them
#: was recorded by hand (`gate record ... --evidence`) and its note is all there is.
TOOL_KEYS = ("command", "reviewer", "covered_by", "roborev", "branch_head", "ci_scope", "scope")


def _family(ev: dict[str, Any], cfg) -> str:
    """The family `ddflow review` recorded, else the one the project maps the model to."""
    model = str(ev.get("model") or "")
    return str(ev.get("family") or (family_of(model, cfg) if model else ""))


def _row(gate: str, rec, cfg) -> dict[str, Any]:
    ev = rec.evidence or {}
    hand = rec.outcome in ("passed", "failed", "partial") and not any(k in ev for k in TOOL_KEYS)
    row: dict[str, Any] = {
        "gate": gate,
        "outcome": rec.outcome,
        "by": rec.by,
        "reason": (rec.reason or "")[: cfg.session.report_note_chars],
        "model": str(ev.get("model") or ""),
        "family": _family(ev, cfg),
        "reviewer": str(ev.get("reviewer") or ""),
        "tree": str(ev.get("tree_sha") or "")[:14],
        "by_hand": hand,
        "note": str(ev.get("note") or "")[: cfg.session.report_note_chars] if hand else "",
        "branch_head": str(ev.get("branch_head") or ""),
        "rounds": ev.get("rounds") or 0,
        "findings": ev.get("findings"),
        "reviewed_sha": str(ev.get("reviewed_sha") or ""),
    }
    if ev.get("covered_by"):
        row["covered_by"] = str(ev["covered_by"])
    return row


def rows(st, cfg, it, events) -> list[dict[str, Any]]:
    """One row per recorded gate, in pipeline order (then any gate outside it), with the
    triage counts of a review gate."""
    order = G.pipeline_for(it, cfg)
    names = [g for g in order if g in it.gates] + [g for g in it.gates if g not in order]
    out = []
    for g in names:
        row = _row(g, it.gates[g], cfg)
        counts = G.triage_counts(it, g)
        if counts:
            row["triage"] = counts
        out.append(row)
    return out


def earlier_tree(st, cfg, item_id: str, repo: Path, landed: str) -> dict[str, str]:
    """gate -> why, for each gate whose pass describes another tree than the one that landed
    (the note `complete` prints). Empty when git cannot say."""
    try:
        notes = G.stale_evidence_detail(st, cfg, item_id, repo, landed=landed)
    except Exception:
        return {}
    return {
        n.gate: (("could not be checked: " if n.unverified else "") + n.why)[
            : cfg.session.report_note_chars
        ]
        for n in notes
    }


def overrides(it, led: dict[str, Any], tests: list[dict], rows_: list[dict]) -> list[str]:
    """Every excuse on the record: a forced completion and what it overrode, skipped gates,
    a forced review round and its reason, a regression check that was overridden."""
    out = []
    if led.get("forced"):
        out.append(
            "complete --force overrode: " + "; ".join(led.get("overridden") or ["(nothing named)"])
        )
    for g, rec in it.gates.items():
        if rec.outcome == "skipped":
            out.append(f"gate {g} skipped: {rec.reason or 'no reason recorded'}")
    for run in it.gate_history:
        why = (run.evidence or {}).get("budget_forced")
        if why:
            out.append(f"review {run.gate} forced past the round budget: {why}")
    for t in tests:
        if t["verified"] == "overridden":
            out.append(
                f"{t['bug']}: regression check skipped (--skip-regression-verify): {t['reason']}"
            )
        elif t["verified"] == "could-not-run":
            out.append(f"{t['bug']}: regression check could not run: {t['reason']}")
    return out


def not_done(
    it, led: dict[str, Any], rows_: list[dict], pipeline: list[str], tests: list[dict]
) -> list[str]:
    """What the record says was NOT done: a pipeline gate with no pass, a bug still open, an
    item that did not land, a review finding with no verdict."""
    out = []
    state = {r["gate"]: r["outcome"] for r in rows_}
    for g in pipeline:
        if state.get(g) != "passed":
            out.append(f"gate {g}: {state.get(g) or 'never recorded'}")
    out += [f"{t['bug']} is still open" for t in tests if t["open"]]
    if not (it.merged_sha or led.get("sha")):
        out.append("not merged")
    if it.state != "done":
        out.append(f"not completed (state {it.state})")
    for g in it.gates:
        counts = G.triage_counts(it, g)
        if counts and counts["untriaged"]:
            out.append(f"review {g}: {counts['untriaged']} finding(s) untriaged")
    return out
