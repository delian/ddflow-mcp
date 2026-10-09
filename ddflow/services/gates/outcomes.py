"""Recorded gate outcomes: status of an item's pipeline, stale evidence, recording and human approval."""

from __future__ import annotations

import getpass
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import GATE_OUTCOMES, OUTCOME_MARK, State
from ...infra import hostinfo as H
from ...infra.log import EventLog
from .defs import GateDef, pipeline_for, required_gates
from .evidence import (
    TreeEntries,
    commit_tree_entries,
    content_id,
    differing_paths,
    normal_fingerprint,
    recorded_content,
    tree_fingerprint,
    worktree_entries,
)


@dataclass
class GateStatus:
    item: str
    pipeline: list[str]
    done: list[str]
    current: str
    blocked_by: list[str]
    unavailable: list[str]
    skipped: list[str]
    complete: bool
    #: Pipeline gates with NO recorded outcome at all. Silence is its own state: it is
    #: neither a pass nor a failure, and `gates.require_outcome` decides whether it
    #: blocks completion.
    silent: list[str] = field(default_factory=list)
    #: gate -> "3 finding(s): 2 refuted, 1 confirmed, 0 untriaged" for a recorded review
    #: that reported findings (`triage_counts`), with "-- PASSED ON REFUTATION" when the
    #: triage that settled its last finding after the round cap recorded the pass
    #: (`on_refutation`). Counting changes no outcome; only that settling triage does.
    triage: dict[str, str] = field(default_factory=dict)
    #: gate -> "1 full round, 2 delta rounds" for a gate `ddflow review` has reviewed.
    rounds: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        return "\n".join(
            f"  [{OUTCOME_MARK.get(o, ' ')}] {g}"
            + (f"  -- {self.triage[g]}" if g in self.triage else "")
            + (f"  -- {self.rounds[g]}" if g in self.rounds else "")
            for g, o in self.rows
        )

    rows: list[tuple[str, str]] = field(default_factory=list)


def triage_counts(it, gate: str) -> dict[str, int] | None:
    """How the findings of ``gate``'s recorded review stand: ``{"findings", "refuted",
    "confirmed", "untriaged"}``, or None when it reported none -- or is not a `ddflow
    review` that numbered them (a hand-recorded gate, or one from before triage).

    A finding is triaged when a `review.triaged` event names the DIGEST of its exact text,
    so a re-review carries a triage over only when the finding is unchanged.
    """
    rec = it.gates.get(gate)
    found = (rec.evidence or {}).get("chunk_findings") if rec else None
    if not found or not all(f.get("digest") for f in found):
        return None
    mine = it.triage.get(gate, {})
    verdicts = [mine.get(f["digest"], {}).get("verdict", "") for f in found]
    return {
        "findings": len(found),
        "refuted": verdicts.count("refuted"),
        "confirmed": verdicts.count("confirmed"),
        "untriaged": sum(1 for v in verdicts if v not in ("refuted", "confirmed")),
    }


def rounds_line(it, gate: str) -> str:
    """ "1 full round, 2 delta rounds" from the gate's recorded `ddflow review`, or ""."""
    rec = it.gates.get(gate)
    ev = (rec.evidence or {}) if rec else {}
    if "review_kind" not in ev:
        return ""
    full, delta = int(ev.get("rounds") or 0), int(ev.get("delta_rounds") or 0)
    if not full and not delta:
        return ""

    def n(k: int, what: str) -> str:
        return f"{k} {what} round{'' if k == 1 else 's'}"

    return f"{n(full, 'full')}, {n(delta, 'delta')}"


def on_refutation(it, gate: str) -> dict[str, Any] | None:
    """The `passed_on_refutation` flag of ``gate``'s recorded PASS (its refuted and
    confirmed counts and the rounds it took), or None: passed by a clean review, or not
    passed (decision D-unify 5: such a pass is allowed, and always visible)."""
    rec = it.gates.get(gate)
    if rec is None or rec.outcome != "passed":
        return None
    flag = (rec.evidence or {}).get("passed_on_refutation")
    return flag if isinstance(flag, dict) else None


def _pass_mark(it, gate: str) -> str:
    """The suffix a gate's triage line carries: its flagged pass on refutation, or a pass
    on findings that were all confirmed and fixed (B1396d7bd55), else nothing."""
    if on_refutation(it, gate):
        return " -- PASSED ON REFUTATION"
    rec = it.gates.get(gate)
    if rec is not None and rec.outcome == "passed" and (rec.evidence or {}).get("findings_fixed"):
        return " -- findings fixed"
    return ""


def refuted_passes(state: State, item_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Every gate recorded passed ON REFUTATION, for the operator's spot-check (D-unify 5):
    one row per (item, gate) with the flag's own counts and rounds. ``item_ids`` narrows it
    to those items; removed items are skipped. Each row carries ``at``, when the pass
    was recorded. Ordered by item id, then gate name."""
    wanted = None if item_ids is None else set(item_ids)
    rows: list[dict[str, Any]] = []
    for it in sorted(state.items.values(), key=lambda i: i.id):
        if it.removed or (wanted is not None and it.id not in wanted):
            continue
        for gate in sorted(it.gates):
            flag = on_refutation(it, gate)
            at = it.gates[gate].at
            if flag is not None:
                rows.append(
                    {
                        "item": it.id,
                        "title": it.title,
                        "state": it.state,
                        "gate": gate,
                        "at": at,
                        **flag,
                    }
                )
    return rows


def refuted_line(row: dict[str, Any]) -> str:
    """One row of `refuted_passes` as a line: ``T1.critic  2 refuted, 0 confirmed, 2 round(s)``."""
    rounds = row.get("rounds")
    tail = f", {rounds} round(s)" if rounds is not None else ""
    return f"{row['item']}.{row['gate']}  {row.get('refuted', 0)} refuted, {row.get('confirmed', 0)} confirmed{tail}"


def triage_line(counts: dict[str, int]) -> str:
    return (
        f"{counts['findings']} finding(s): {counts['refuted']} refuted, "
        f"{counts['confirmed']} confirmed, {counts['untriaged']} untriaged"
    )


def status(state: State, cfg: Config, item_id: str) -> GateStatus:
    """Where is this item in its pipeline, and what is the next thing to do?"""
    it = state.items.get(item_id)
    if it is None:
        raise KeyError(item_id)
    gates = pipeline_for(it, cfg)
    rows = [(g, it.gate_outcome(g)) for g in gates]
    required = required_gates(cfg)
    settled = {g: it.gate_satisfied(g, g in required) for g in gates}
    done = [g for g in gates if settled[g]]
    blocked = [g for g, o in rows if o == "failed"]
    unavail = [g for g, o in rows if o in ("unavailable", "partial")]
    skipped = [g for g, o in rows if o == "skipped"]
    current = next((g for g in gates if not settled[g]), "")
    complete = all(settled.values())
    return GateStatus(
        item=item_id,
        pipeline=gates,
        done=done,
        current=current,
        blocked_by=blocked,
        unavailable=unavail,
        skipped=skipped,
        complete=complete,
        rows=rows,
        silent=[g for g, o in rows if not o],
        triage={
            g: triage_line(c) + _pass_mark(it, g) for g, _o in rows if (c := triage_counts(it, g))
        },
        rounds={g: line for g, _o in rows if (line := rounds_line(it, g))},
    )


def stale_evidence(
    state: State, cfg: Config, item_id: str, cwd: Path, *, landed: str = ""
) -> list[str]:
    """Gates whose evidence is KNOWN to describe a tree that has since changed.

    Stale only: a gate whose evidence could not be compared at all is NOT in this list,
    and an empty list therefore does not mean "all evidence is fresh". A caller that
    needs that distinction -- `complete` does -- reads `stale_evidence_detail`, where
    such a gate is a note with ``unverified`` set.
    """
    notes = stale_evidence_detail(state, cfg, item_id, cwd, landed=landed)
    return [n.gate for n in notes if not n.unverified]


@dataclass(frozen=True, order=True)
class StaleNote:
    """A passed gate whose evidence is not about the tree being completed -- or, with
    ``unverified``, one whose evidence could not be compared with it at all. The two
    are kept apart: "could not tell" rendered as "fresh" is a silent pass, and rendered
    as "stale" is a false alarm."""

    gate: str
    why: str
    unverified: bool = False


def stale_evidence_detail(
    state: State, cfg: Config, item_id: str, cwd: Path, *, landed: str = ""
) -> list[StaleNote]:
    """A note for each gate whose evidence describes another tree, or cannot be checked.

    The hazard B21 names, and the ordinary way it happens: run the tests, edit one more
    thing, complete. The recorded pass is then true about source nobody is shipping —
    and it is indistinguishable, in the log, from a pass about the code that shipped.

    Compared against ``cwd``'s working tree, or -- with ``landed``, a commit -- against
    what that commit holds. After `merge` the item's worktree is gone, and the tree
    being completed is the branch that landed, not whatever the primary checkout has
    checked out: comparing against the primary made every gate stale on the ordinary
    path (bugs Bd86b05a8f8, Ba84119f707, B613cb67194), and a warning that always fires
    is one nobody reads.

    CONTENT is compared (`source_tree`), not commit ids: a gate run on uncommitted
    edits, or at the branch tip, is fresh evidence about the merge commit that records
    the same files. Evidence from before `source_tree` was recorded falls back to the
    fingerprint against a working tree, and against a commit only when it was taken on
    a clean tree (its commit then names the content).

    Only gates in `gates.evidence_required` are checked. The others legitimately record
    before the work is finished: `implement` is *supposed* to precede the edits that
    follow it, and flagging that would make this noise, which is how a real warning
    stops being read.

    Evidence that names content which cannot be compared now -- the landed commit or
    the worktree unreadable, or legacy evidence taken on uncommitted edits once the
    worktree is gone -- comes back ``unverified``, not silently fresh. Evidence with no
    tree at all (a gate recorded where nothing could be measured) is not reported.
    """
    it = state.items.get(item_id)
    if it is None:
        return []
    label = f"what landed ({landed[:12]})" if landed else "the working tree"
    entries_cache: dict[str, TreeEntries | None] = {}

    def entries_of(rev: str) -> TreeEntries | None:
        if rev not in entries_cache:
            entries_cache[rev] = (
                worktree_entries(cwd) if rev == "" else commit_tree_entries(cwd, rev)
            )
        return entries_cache[rev]

    now = entries_of(landed)
    now_id = content_id(now)
    fingerprint = None if landed else tree_fingerprint(cwd)
    stale = []
    for gid in cfg.gates.evidence_required:
        rec = it.gates.get(gid)
        if not rec or rec.outcome != "passed":
            continue
        ev = rec.evidence or {}
        was_sha = normal_fingerprint(ev.get("tree_sha", "") or "")
        base, _, dirt = was_sha.partition("+")
        was_id = recorded_content(ev.get("tree_sha", "") or "", ev.get("source_tree", "") or "")
        if was_id:
            if not now_id:
                stale.append(StaleNote(gid, f"{label} could not be read", unverified=True))
                continue
            if was_id == now_id:
                continue
        elif dirt == "clean" and base:
            then = commit_tree_entries(cwd, base)
            if then is None or now is None:
                why = f"neither {base} nor {label} could be read as a tree"
                stale.append(StaleNote(gid, why, unverified=True))
                continue
            if not differing_paths(then, now):
                continue
        elif not landed and was_sha and fingerprint:
            if was_sha == fingerprint:
                continue  # legacy evidence on a dirty tree: only the fingerprint can tell
        elif landed and dirt:
            why = (
                f"it ran on uncommitted edits over {base}, recorded before ddflow kept "
                f"their content, so they cannot be compared with {label}"
            )
            stale.append(StaleNote(gid, why, unverified=True))
            continue
        else:
            continue
        stale.append(StaleNote(gid, _what_differs(cwd, base, dirt, now, label)))
    return sorted(stale)


def _what_differs(cwd: Path, base: str, dirt: str, now: TreeEntries | None, label: str) -> str:
    """Name the files, when the tree the gate ran on can be rebuilt (a clean commit)."""
    then = commit_tree_entries(cwd, base) if base else None
    paths = differing_paths(then, now) if then is not None and now is not None else []
    shown = ", ".join(paths[:8]) + (f" (+{len(paths) - 8} more)" if len(paths) > 8 else "")  # noqa: PLR2004
    if dirt == "clean" and paths:
        return f"it ran on {base}; {label} differs in {shown}"
    if paths:
        return (
            f"it ran on uncommitted edits over {base}, and {label} holds other content "
            f"(files changed since {base}: {shown})"
        )
    return f"the content it ran on is not the content of {label}"


def record(  # noqa: PLR0913 -- the caller's evidence and ddflow's measurements are kept apart on purpose
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    outcome: str,
    *,
    by: str = "",
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    gates: dict[str, GateDef] | None = None,
    human: bool = False,
    measured: dict[str, Any] | None = None,
) -> None:
    """Write a gate outcome to the log, enforcing the evidence contract.

    ``evidence`` is what the CALLER supplied; ``measured`` is what ddflow determined
    itself (the tree fingerprint, the diff size). Only the first can satisfy the
    contract: the measured fields are always present, so counting them made every bare
    pass look evidenced (bug Bbc9a7ee3f2). Both are recorded.

    Rejecting a bare pass at the API boundary is deliberate. If the only thing standing
    between "I ran the tests" and a recorded pass is the agent's honesty, then over a
    long run the record measures honesty rather than testing.
    """
    if outcome not in GATE_OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}; expected one of {GATE_OUTCOMES}")
    gdef = (gates or {}).get(gate)
    # Refused HERE, at the service boundary, not in the CLI branch that happens to be
    # the usual caller. A check that lives in one surface is a check the other surface
    # does not have, which is how `ddflow_gate_record` would have cleared a human
    # checkpoint over MCP while the terminal refused it.
    if gdef is not None and gdef.is_human_gate and not human:
        raise ValueError(
            f"{gate!r} is a human-approval gate: it is cleared by a person, not by an "
            f"agent recording that it happened. Ask the operator to run "
            f"`ddflow approve {item_id} {gate}` (or `--reject --reason ...`). "
            f"There is deliberately no MCP tool for this."
        )
    if evidence and evidence.get("reviewer") and not evidence.get("reviewer_digest"):
        # WHICH entry reviewed, as configured right now: what reviewer independence
        # checks against `reviewer.configured`/`reviewer.approved` (D-reviewer-trust).
        # Only `ddflow review` writes a `reviewer` key; `gate record` cannot.
        from .. import reviewer_trust as RT

        if dig := RT.digest_of(log.root, str(evidence["reviewer"])):
            evidence = {**evidence, "reviewer_digest": dig}
    if outcome == "skipped":
        if not cfg.gates.allow_skip_with_reason:
            raise ValueError("skipping is disabled ([gates].allow_skip_with_reason)")
        if not reason:
            raise ValueError("a skip must carry --reason; an unexplained skip is invisible")
    if outcome == "passed" and gdef and gdef.evidence and not evidence:
        raise ValueError(
            f"gate {gate!r} requires evidence to pass (it is in gates.evidence_required). "
            f"Attach the command, its exit code and its output — or record "
            f"`unavailable` with a reason, which is an honest result."
        )
    if outcome in ("unavailable", "partial", "failed") and not reason:
        raise ValueError(f"outcome {outcome!r} must carry a --reason")
    log.append(
        f"gate.{outcome}",
        item_id,
        {
            "gate": gate,
            "by": by or log.agent_id,
            "reason": reason,
            "evidence": {**(measured or {}), **(evidence or {})},
        },
    )


def approve(
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    *,
    gates: dict[str, GateDef] | None = None,
    note: str = "",
    reject: bool = False,
    reason: str = "",
) -> str:
    """A PERSON clears (or refuses) a human gate. Returns the line to print.

    Records the OS user rather than the agent id, and stamps `human: true` on the
    evidence. Neither makes forgery impossible — an agent with a shell can run this —
    but both make a forged approval *visible* in the log instead of identical to a real
    one, which is the difference between a record you can audit and one you cannot.

    A rejection is a first-class outcome, not the absence of an approval: "the operator
    looked and said no" and "nobody has looked yet" are different states, and an item
    sitting in the second forever is how a checkpoint becomes a silent stall.
    """
    gdef = (gates or {}).get(gate)
    if gdef is None:
        raise ValueError(f"no such gate {gate!r}")
    if not gdef.is_human_gate:
        raise ValueError(
            f"{gate!r} is not a human-approval gate, so there is nothing for a person "
            f"to approve. Set `[gate.{gate}] human = true` in .ddflow/gates.toml if it "
            f"should be one; otherwise use `ddflow gate record`."
        )
    if reject and not reason:
        raise ValueError("a rejection must carry --reason: 'no' with no reason cannot be acted on")

    try:
        who = getpass.getuser()
    except Exception:
        # Not fatal, and not silently blank: an approval whose approver is unknown is
        # still a real approval, and saying "unknown" is honest where inventing a name
        # would not be.
        who = "unknown-user"
    ev = {
        "human": True,
        "approved_by": who,
        "host": H.short_host(),
        "note": note,
    }
    outcome = "failed" if reject else "passed"
    record(
        log,
        cfg,
        item_id,
        gate,
        outcome,
        by=who,
        reason=reason,
        evidence=ev,
        gates=gates,
        human=True,
    )
    verb = "REJECTED" if reject else "approved"
    tail = f" — {reason}" if reason else (f" — {note}" if note else "")
    return f"{item_id}.{gate} {verb} by {who}{tail}"
