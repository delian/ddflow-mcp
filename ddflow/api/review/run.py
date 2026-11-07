"""The review entry points: one gate, or several gates in one call."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...services import gates as GD
from ...services import prompts as P
from ...services import review as RV
from ...services import review_exclude as REX
from ...services import review_manifest as RM
from ...services.gates import running as RUN
from ...services.gates.rounds import delta_rounds as _delta_rounds
from ...services.gates.rounds import full_rounds as _full_rounds
from ...services.gates.rounds import rounds_used as _rounds_used
from .._base import _load
from ..lifecycle.author import _author_model
from ..review_supersede import round_evidence
from .context import (
    _BACKGROUND_HINT,
    _converges,
    _floor_for,
    _with_guidance,
    _with_previous_findings,
)
from .delta import _head_of
from .diff import diff_for
from .findings import _announce_rerun, _link_refuted
from .gate import (
    _claim_or_refuse_twin,
    _early_refusal,
    _ends_on_signal,
    _intent_missing,
    _log_started,
    _misplaced,
    _name_the_item,
    _record_review,
    _say_selection,
    _unknown_item,
)
from .records import (
    PROGRESS_EVERY_S,
    _blocking_findings,
    _finding_rows,
    _gate_outcome,
    _item_intent,
    _lease_ticker,
    _record,
    _say_result,
)
from .reviewers import _ReplyFile
from .scope import _budget, _cite_grant, _justification, _mark_partial_retry, _scope
from .triaging import _reviewed_snapshot, _why_outcome


@_ends_on_signal
def review(  # noqa: PLR0913 -- what to diff is one of commit | branch | the item's tree, and called_from says where the caller stands
    repo: Path,
    *,
    gate: str = "critic",
    item: str = "",
    intent: str = "",
    context: str = "",
    base: str = "",
    on_progress: Callable[[str], None] | None = None,
    commit: str = "",
    branch: str = "",
    called_from: Path | None = None,
    agent: str = "",
    chunks: list[int] | list[str] | str | None = None,
    delta: bool = False,
    force: bool = False,
    reason: str = "",
    full: bool = False,
    exclude: str = "",
    include_pinned: bool = False,
) -> O.Outcome:
    """Run the reviewers configured for `gate`, and record the outcome against `item`.

    `gate` may name several gates, comma-separated (`rubber_duck,critic`): under
    `[review].combined_under_lines` changed lines that is ONE review recorded for each,
    otherwise one review per gate (`_review_gates`, decision D-gate-economy 2).
    One gate is `_review_gate`, whose docstring has the rest.
    """
    args = dict(locals())  # first: every later local would leak into the kwargs
    if (refused := _unknown_item(repo, item, agent, gate)) is not None:  # a refusal is falsy
        return refused
    refused = _misplaced(repo, item, agent, gate, branch, commit, called_from, force, reason)
    if refused is not None:  # a refusal is falsy
        return refused
    asked = list(dict.fromkeys(g.strip() for g in gate.split(",") if g.strip()))
    _l, _c, _st = _load(repo, agent)
    taken = _head_of(repo, _st.items.get(item), branch, commit)
    marker, twin_refusal = _claim_or_refuse_twin(repo, item, taken)
    if twin_refusal is not None:  # a refusal is falsy
        return twin_refusal
    try:
        if len(asked) > 1:
            return _review_gates(asked, args)
        return _review_gate(**{**args, "gate": asked[0] if asked else gate})
    finally:
        RUN.clear_started(marker)


_LENSES = {
    "rubber_duck": "refute the change: find the input that makes it wrong",
    "critic": "criticise it: a wrong design, a missing or vacuous test, a promise the "
    "code does not keep",
}


def _review_gates(asked: list[str], args: dict[str, Any]) -> O.Outcome:
    """Several gates in one call: combined when the change is small, else one by one.

    Combined: ONE full review whose intent names every gate's lens, recorded for each gate
    with the same findings and a shared `review_id` (each gate's findings are triaged on
    that gate). Only for a plain review of known lenses (`_LENSES`), served by the same
    reviewers, with every gate in budget: `--chunk`, `--delta`, `--commit` and `--base` work on one gate's record, and a
    gate out of rounds must be refused on its own -- so those run gate by gate. The change
    is measured as the changed lines of the diff a full review would send.
    """
    repo, item = args["repo"], args["item"]
    log, cfg, st = _load(repo, args["agent"])
    under = int(cfg.review.combined_under_lines or 0)
    plain = not (args["chunks"] or args["delta"] or args["commit"] or args["base"])
    alike = all(g in _LENSES for g in asked) and _same_reviewers(repo, asked)
    lines = _changed_lines(cfg, st, args) if plain and alike and under > 0 else 0
    unjustified = any(
        not _justification(repo, cfg, st, st.items.get(item), {**args, "gate": g})[0] for g in asked
    )
    if (
        0 < lines < under
        and not unjustified
        and not _any_out_of_rounds(cfg, st.items.get(item), asked, args)
    ):
        return _review_combined(asked, args, lines, under, log, cfg)
    return _review_each(asked, args)


def _same_reviewers(repo: Path, gates: list[str]) -> bool:
    """Is every gate served by the same reviewers? A combined review runs the FIRST gate's,
    so a gate with a reviewer of its own must not be recorded from another gate's (or as
    unavailable because the first gate has none)."""
    revs, dispatch = RV.load_reviewers(repo), RV.ReviewerDispatch()
    names = [sorted(r.name for r in dispatch.select(revs, g).reviewers) for g in gates]
    return all(n == names[0] for n in names)


def _changed_lines(cfg, st, args: dict[str, Any]) -> int:
    """Added plus removed lines of the diff a full review of ``args`` would send: lines
    inside a hunk only, so a content line that itself starts with `++` or `--` counts and a
    file header never does."""
    diff, _how = diff_for(
        args["repo"], cfg, st, args["item"], args["base"],
        branch=args["branch"], called_from=args["called_from"],
    )  # fmt: skip
    n, in_hunk = 0, False
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            in_hunk = False
        elif ln.startswith("@@"):
            in_hunk = True
        elif in_hunk and ln[:1] in ("+", "-"):
            n += 1
    return n


def _any_out_of_rounds(cfg, it, gates: list[str], args: dict[str, Any]) -> bool:
    """Would a full round of any of ``gates`` be refused? Then none is combined."""
    if it is None:
        return False
    # Operator grants are item.gate scoped; a combined round spans several gates, so none is
    # consulted here: past the cap the gates are reviewed one by one, and each cites its own.
    quiet = lambda _line: None  # noqa: E731 -- the gate's own run says it, if it comes to that
    return any(
        _budget(cfg, it.id, g, _rounds_used(it, g), args["force"], args["reason"], quiet)[0]
        for g in gates
    )


def _review_combined(asked, args, lines: int, under: int, log, cfg) -> O.Outcome:
    say_out: list[str] = []

    def say(line: str) -> None:
        say_out.append(line)
        if args["on_progress"]:
            args["on_progress"](line)

    _l, _c, st = _load(args["repo"], args["agent"])
    item = args["item"]
    rid = secrets.token_hex(6)
    names = " and ".join(asked)
    intent = (args["intent"] or _item_intent(st.items.get(item))).strip()
    intent += (
        f"\n\nCombined review (decision D-gate-economy 2): this ONE review is recorded for the "
        f"{names} gates, so apply each one's lens -- "
        + "; ".join(f"{g}: {_LENSES[g]}" for g in asked)
        + "."
    )
    say(
        f"combined review {rid}: {lines} changed line(s), under [review].combined_under_lines "
        f"= {under}, so one review stands for {names}"
    )
    # A FULL round whatever `delta_default` says: the other gates copy its record, and a
    # delta's record is merged with the first gate's own earlier findings.
    out = _review_gate(
        **{
            **args,
            "gate": asked[0],
            "intent": intent,
            "full": True,
            "extra_evidence": {"review_id": rid, "combined_gates": asked},
        }
    )
    _l, _c, st = _load(args["repo"], args["agent"])
    rec = st.items[item].gates.get(asked[0]) if item in st.items else None
    gates = GD.load_gates(args["repo"], cfg)
    if rec is not None and (rec.evidence or {}).get("review_id") == rid:
        for g in asked[1:]:
            ev = {**rec.evidence, **_rounds_for(st.items[item], g, rec.evidence)}
            # a copy of the first gate's record, its measurement included
            GD.record(
                log,
                cfg,
                item,
                g,
                rec.outcome,
                by=rec.by,
                reason=rec.reason,
                evidence=ev,
                gates=gates,
            )
            found = ev.get("chunk_findings")
            linked = _link_refuted(
                log, st.items.get(item), g, found if isinstance(found, list) else []
            )
            if linked:
                say(
                    f"recorded {item}.{g} = {rec.outcome} (the same combined review {rid}); "
                    f"auto-linked finding(s) {', '.join(linked)} to the item's refuted triage"
                )
                continue
            say(f"recorded {item}.{g} = {rec.outcome} (the same combined review {rid})")
    elif item and out.data.get("outcome") == "unavailable":
        # No review happened (no reviewer, an empty diff): each gate says so, not only the
        # first -- a gate that was asked for and shows nothing reads as never run.
        for g in asked[1:]:
            GD.record(log, cfg, item, g, "unavailable", reason=out.reason, gates=gates)
            say(f"recorded {item}.{g} = unavailable (the combined review {rid} could not run)")
    _l, _c, st = _load(args["repo"], args["agent"])
    if line := _charged_line(cfg, st.items.get(item), asked, rec):
        say(line)
    data = {**out.data, "gates": asked, "review_id": rid}
    data["text"] = "\n".join([say_out[0], data.get("text", ""), *say_out[1:]]).strip()
    return O.Outcome(kind=out.kind, data=data, exit=out.exit, reason=out.reason)


def _charged_line(cfg, it, asked: list[str], rec) -> str:
    """What a combined review cost EACH gate (one 'rounds used' line cannot say which): a
    full round, unless it reached no reviewer or was the free retry of a partial one."""
    if it is None:
        return ""
    cap = cfg.review.max_rounds
    each = ", ".join(f"{g} {_rounds_used(it, g)}/{cap if cap > 0 else 'unlimited'}" for g in asked)
    ev = (rec.evidence or {}) if rec is not None else {}
    charged = ev.get("review_kind") == "full" and not ev.get("retry_of_partial")
    what = "one full round to each gate" if charged else "no round to any gate"
    return f"combined review charged {what}: rounds used now {each}"


def _rounds_for(it, gate: str, ev: dict[str, Any]) -> dict[str, Any]:
    """The round counts of ``gate``'s own record: the combined review is one more full
    round of each gate it stands for, and leaves each gate's delta count as it was."""
    counted = ev.get("review_kind") == "full" and not ev.get("retry_of_partial")
    done = _full_rounds(it, gate)
    out: dict[str, Any] = {
        "rounds": done + (1 if counted else 0),
        "delta_rounds": _delta_rounds(it, gate),
    }
    if counted:
        out["round"] = done + 1
    return out


def _review_each(asked: list[str], args: dict[str, Any]) -> O.Outcome:
    """One review per gate, one after the other: the event log's read caches and its
    transaction guard are per process and unlocked, so two reviews in threads could fold
    an event twice or append outside the lock (why MCP offloads to a PROCESS). Each review
    already sends its chunks at once."""
    outs = [_review_gate(**{**args, "gate": g}) for g in asked]
    worst = max(outs, key=lambda o: (o.exit != O.OK, o.exit))
    text = "\n\n".join(
        f"== {g}\n{o.data.get('text', o.reason)}" for g, o in zip(asked, outs, strict=True)
    )
    data = {
        **worst.data,
        "gates": asked,
        "text": text,
        "by_gate": {g: o.data for g, o in zip(asked, outs, strict=True)},
    }
    return O.Outcome(kind="review", data=data, exit=worst.exit, reason=worst.reason)


def _shed_degraded(revs, st, gate: str, cooldown_s: float, say) -> tuple[list, list]:
    """(reviewers, shed names) -- the gate's pool with reviewers a quota/exhaustion wall
    already marked (``RV.degraded_marks``) dropped, and the shed names said, in pool order
    (the next enabled cross-family reviewer serves). An unmarked reviewer is never dropped:
    it still gets one run to fail honestly before being marked.

    ``cooldown_s`` is `[review].degraded_cooldown_s`: a reviewer is shed only while its
    mark is younger than that many seconds. A mark at or past the cooldown is EXPIRED,
    so the reviewer is re-probed (and re-marked if the wall is still up) instead of shed
    forever -- a transient 429 used to permanently disable a cross-family reviewer
    (bug B066719c493). <= 0 re-probes on every review (the mark blocks no run).
    """
    degraded = {
        name: row
        for name, row in RV.active_degraded_marks(st, cooldown_s).items()
        if any(r.name == name for r in revs)
    }
    for name in sorted(degraded):
        say(f"→ shed {name}: degraded ({degraded[name].get('reason') or 'quota/exhaustion'})")
    return [r for r in revs if r.name not in degraded], sorted(degraded)


def _note_degraded(res, r, gate: str, cooldown_s: float, extra_evidence: dict, say) -> None:
    """Append the quota/exhaustion mark of one review to `extra_evidence` and SAY it, so a
    degraded reviewer is recorded and surfaced instead of silently folded into
    `unavailable` (B8efd3d1bc4). No mark for anything but a reviewer's own quota wall.

    The mark carries ``at`` (the epoch second it was written) so it can EXPIRE after
    `[review].degraded_cooldown_s` and the reviewer is re-probed (bug B066719c493)."""
    if not res.degraded:
        return
    if extra_evidence is None:
        extra_evidence = {}
    extra_evidence.setdefault("review_degraded", []).append(
        {
            "reviewer": r.name,
            "reason": res.degraded,
            "gate": gate,
            "model": r.model,
            "family": r.resolved_family(),
            "at": time.time(),
        }
    )
    say(
        f"→ {r.name} quota/exhaustion ({res.degraded}): marked degraded and shed from "
        f"{gate} -- re-probed after {cooldown_s}s once the mark expires (the next enabled "
        f"cross-family reviewer serves meanwhile)"
    )


#: Dispatch rank by independence tier: a reviewer OUTSIDE the author's family first, a
#: different model of the same family next, and one that establishes nothing (the
#: author's own model, or a model no map recognises) last. Falls back to the tail for a
#: tier `review_tier` never returns, so an unknown enough reviewer still runs, just last.
_TIER_RANK = {"family": 0, "model": 1, "none": 2}


def _order_cross_family_first(revs, author_model: str, cfg, say) -> list:
    """`revs` with the fast cross-family tier led, so a slow same-family reviewer is not
    paid for before an outside-family one can serve (bug B19066e802f), and a reviewer
    that establishes nothing runs last instead of first in config order.

    The sort is STABLE: within a tier, config order survives, so order only yields when a
    tier makes it. When the author cannot be named here (`[agent].family` and the session
    model are both empty) `review_tier` returns `none` for every reviewer and the pool
    keeps its config order -- a gate must not reorder on a guess about independence.

    Degraded/429 reviewers are shed BEFORE this runs (`_shed_degraded`), so the cascade
    B8efd3d1bc4 dropped decides the pool's members and this only orders what remains.
    """

    def rank(r):
        return _TIER_RANK.get(GD.review_tier(cfg, author_model, r.model, r.family), 2)

    ordered = sorted(revs, key=rank)
    if [r.name for r in ordered] != [r.name for r in revs]:
        say("→ order (cross-family first): " + ", ".join(r.name for r in ordered))
    return ordered


def _identifying_reviewer(results, author_model: str, cfg) -> Any:
    """The review recorded as the gate's identifying pass.

    The best review is the one with the fewest outstanding problems (its status is the
    minimum of the run); when several tie -- every reviewer came back clean, the common
    case -- a reviewer from a DIFFERENT family than the author is the one named, so a
    clean cross-family review is not discarded when a same-family reviewer also ran
    clean and happened to be listed first (bug Be2bb7ea786). The author is resolved as
    `complete` resolves it (`[agent].family`, else the session model). The reviewer's
    family is resolved as `review_tier` resolves it (declared family, else its model).
    """
    best = min(results, key=lambda r: r.status)
    tied = [r for r in results if r.status == best.status]
    cross = [r for r in tied if GD.review_tier(cfg, author_model, r.model, r.family) == "family"]
    if cross:
        return cross[0]
    # When the author cannot be named here (`[agent].family` and the session model are
    # both empty) `review_tier` returns `none` for every reviewer, so `cross` is empty
    # and the first tied reviewer would be the author's own family when the lane also
    # serves its model (bug B2e5ca93520). The author then has exactly ONE family, so
    # when the clean reviewers span two or more distinct families, name a reviewer whose
    # family differs from the tied-first one. This is a heuristic: with the author
    # unknown no choice provably names the cross-family reviewer, and `reviewer_families`
    # still carries every family so `complete` reads the cross-family panel either way
    # (bug B1a30dc3303).
    first = _fam(tied[0]) or GD.family_of(tied[0].model, cfg).strip().lower()
    fams = {_fam(r) or GD.family_of(r.model, cfg).strip().lower() for r in tied}
    fams.discard("")
    if len(fams) > 1:
        for r in tied:
            fam = _fam(r) or GD.family_of(r.model, cfg).strip().lower()
            if fam and fam != first:
                return r
    return best


def _fam(r) -> str:
    """A reviewer's family as `review_tier` resolves it: a declared family, `unknown`
    normalised to ``""`` so the model's family is consulted, stripped and lowercased."""
    fam = (getattr(r, "family", "") or "").strip().lower()
    return "" if fam == "unknown" else fam


def _failing_reviewer(results, it, gate, converging, cfg, identifying, author_model: str = ""):
    """The reviewer whose finding blocked the gate (bug B3113b1f522), else ``identifying``.
    A failed review must name the reviewer that found the blocking defect, not a clean peer
    that ranked first (or the author's own family, listed first), and must not re-purpose a
    PARTIAL reviewer as the identifying pass (which would flip the run's exit code). The
    findings `_blocking_findings` returns are the SAME objects each reviewer's ``findings``
    held (matched by identity), so the return is one of ``results`` or ``identifying``. The
    same ``author_model`` as `_gate_outcome` decides the discount (bug B7dbf432caa): a
    same-family finding discounted over a clean cross-family verdict must not make the
    same-family reviewer the named failing one on a passed gate."""
    blocking, _ = _blocking_findings(results, it, gate, converging, cfg, author_model)
    for r in results:
        if any(b is f for b in blocking for f in r.findings):
            return r
    return identifying


def _review_gate(  # noqa: PLR0913, C901 -- what to diff is one of commit | branch | the item's tree, and called_from says where the caller stands
    repo: Path,
    *,
    gate: str = "critic",
    item: str = "",
    intent: str = "",
    context: str = "",
    base: str = "",
    on_progress: Callable[[str], None] | None = None,
    commit: str = "",
    branch: str = "",
    called_from: Path | None = None,
    agent: str = "",
    chunks: list[int] | list[str] | str | None = None,
    delta: bool = False,
    force: bool = False,
    reason: str = "",
    full: bool = False,
    exclude: str = "",
    include_pinned: bool = False,
    extra_evidence: dict[str, Any] | None = None,
) -> O.Outcome:
    """Run every reviewer configured for `gate`, and record the outcome against `item`.

    A FULL round -- anything that can cover the item's whole diff -- and a `delta` (only
    what changed since the head the gate's last review covered, as is a `commit`/`base`
    at or after that head) both count against `[review].max_rounds` (decision
    D-gate-economy 2); a `--chunk` re-run and triage never do.
    `force` with a `reason` runs a round past the cap (a full one, or a delta with `delta`),
    recorded in the evidence.

    A plain review of a gate that already has a recorded review is a FULL re-review whose
    prompt carries the previous findings and their triage (`_with_previous_findings`,
    decision D-gate-economy 3); with `[review].delta_default` (off unless a project turns
    it on) it is a `delta` instead, and `full` forces a full round. A delta's findings are
    merged into the gate's record (`_merge_delta`).

    `chunks` re-reviews only those chunks (numbered as the recorded review printed
    them) and merges the result into that record -- see `_rerun_scope`.

    `commit` reviews that one landed commit instead of the item's branch -- the
    after-merge review, recorded against the item (done or not) all the same. `branch`
    reviews that branch against base, for an item claimed without a worktree (see
    `diff_for` for how the branch is otherwise found).
    """

    # Collected AND forwarded. The CLI prints each line as it happens so a two-minute
    # review does not look like a hang; the MCP tool has no live channel, so the same
    # lines become its body — which is what its PROSE_TOOLS entry has always promised
    # ("reviewer findings, already formatted with their severities"). One transcript, two
    # deliveries, rather than a second rendering of the same run.
    transcript: list[str] = []

    def say(line: str) -> None:
        transcript.append(line)
        if on_progress:
            on_progress(line)

    log, cfg, st = _load(repo, agent)
    gates = GD.load_gates(repo, cfg)

    def unavailable(reason: str, **extra) -> O.Outcome:
        """Record it, then report it. An unrecorded UNAVAILABLE is indistinguishable
        from a gate nobody ran, which is how "we reviewed it" becomes true on paper."""
        if item:
            _record(
                repo, log, cfg, st, item, gate, "unavailable", called_from, gates, reason=reason
            )
        return O.nothing(
            "review",
            reason,
            id=item,
            gate=gate,
            outcome="unavailable",
            findings=[],
            text=reason,
            **extra,
        )

    refused = _early_refusal((full, force, delta), log, cfg, st, item, gate, say, gates, repo)
    if refused is not None:
        return refused

    extra_evidence = _name_the_item(repo, cfg, st, item, gate, agent, say, extra_evidence)
    dispatch = RV.ReviewerDispatch()
    selected = dispatch.select(RV.load_reviewers(repo), gate, gates.get(gate))
    revs = selected.reviewers
    _say_selection(selected, say)
    if not revs:
        return unavailable(
            f"No reviewer is configured for gate {gate!r}. "
            + "".join(f"{n}. " for n in selected.notes)
            + "`ddflow reviewers detect --write` finds local models.\n"
            "Recording UNAVAILABLE — which is NOT a pass.",
            how="",
        )

    it = st.items.get(item)
    author_model, _ = _author_model(st, agent, "", cfg)
    revs, shed = _shed_degraded(revs, st, gate, cfg.review.degraded_cooldown_s, say)
    if not revs:
        return unavailable(
            f"Every reviewer for gate {gate!r} is degraded ("
            + ", ".join(shed)
            + ") and was shed. Recording UNAVAILABLE — which is NOT a pass. "
            + "`ddflow doctor` names the degraded reviewers.",
            how="",
        )
    revs = _order_cross_family_first(revs, author_model, cfg, say)
    # The head the diff is taken from, captured BEFORE the diff (B4f5be8179f): a commit
    # landing in between is then in the diff AND after this head, so it is reviewed again
    # by the next delta rather than counted as reviewed by none.
    taken = _head_of(repo, it, branch, commit)
    # ...and the content the reviewer is sent is read now too (B44548e189d): `reviewed_paths`
    # taken when the round is recorded would claim as reviewed what changed meanwhile.
    snapshot = _reviewed_snapshot(repo, cfg, st, it, called_from)
    sargs: dict[str, Any] = {
        "item": item,
        "gate": gate,
        "chunks": chunks,
        "delta": delta,
        "commit": commit,
        "base": base,
        "branch": branch,
        "called_from": called_from,
        "force": force,
        "reason": reason,
        "full": full,
    }
    kind, done, forced, diff, how, why, rerun = _scope(
        repo, cfg, st, log, it, say, revs, locals_=sargs
    )
    supers, fix = sargs.get("supers") or set(), bool(sargs.get("fix"))
    extra_evidence = round_evidence(extra_evidence, supers, fix, _delta_rounds(it, gate), say)
    extra_evidence = _cite_grant(extra_evidence, sargs.get("grant", ""))
    extra_evidence = _mark_partial_retry(extra_evidence, sargs)
    if why:
        return _why_outcome(log, cfg, it, gate, how, why, sargs)
    diff, extra_evidence, skipped = REX.apply(
        diff, cfg, repo, say, extra_evidence, it.tags if it else None, exclude, include_pinned
    )
    if not diff.strip():
        return unavailable(
            f"Empty diff ({how}) — nothing to review"
            + skipped.empty_note()
            + ". Recording UNAVAILABLE.",
            how=how,
        )

    intent = intent or _item_intent(it)
    if not intent:
        return _intent_missing(item, gate, how)

    revs, prior, only = _announce_rerun(rerun, revs, item, gate, say)
    context = dispatch.pack(
        context,
        [
            lambda c: _with_previous_findings(
                c,
                log,
                it,
                item,
                gate,
                say,
                kind=kind,
                rerun=prior,
                supers=supers,
                fix=fix,
                floor=_floor_for(cfg, kind, fix),
            ),
            lambda c: _with_guidance(c, cfg, st, item, gate, say),
        ],
    )

    keeps: dict[str, _ReplyFile] = {}
    overrides = P.overrides_from(cfg)
    tick_s = min(PROGRESS_EVERY_S, max(1, cfg.lease.heartbeat_s))
    keep_lease = _lease_ticker(log, cfg, it, tick_s)
    _log_started(log, it, item, gate)
    results = []
    say(_BACKGROUND_HINT)
    for r in revs:
        keep = keeps[r.name] = _ReplyFile(repo, item, gate, r.name)
        say(f"→ {r.name} ({r.resolved_family()}) reviewing {len(diff)} chars from {how}")
        res = RV.review(
            r,
            diff,
            intent,
            context=context,
            repo=repo,
            prompt_overrides=overrides,
            on_progress=say,
            on_tick=keep_lease,
            tick_s=tick_s,
            only=only,
            on_chunk=keep.add if item else None,
            manifest=RM.for_review(diff, skipped, cfg),
            chunk_max_tokens=cfg.review.chunk_max_tokens,
            split_rounds=cfg.review.split_rounds,
        )
        if prior:
            # An ERROR, or a cut that does not match, is refused WITHOUT recording: the
            # record holds every other chunk's coverage, and a later --chunk needs it.
            why = (
                (res.reason or "the re-run errored")
                if res.status == RV.ERROR
                else RV.merge_rerun(prior, res)
            )
            if why:
                return O.Outcome(
                    kind="review", data={"id": item, "gate": gate}, exit=O.REFUSED, reason=why
                )
        results.append(res)
        _say_result(say, res)
        _note_degraded(res, r, gate, cfg.review.degraded_cooldown_s, extra_evidence, say)

    identifying = _identifying_reviewer(results, author_model, cfg)
    outcome, extra_evidence = _gate_outcome(
        identifying, results, it, item, gate, _converges(kind, fix), cfg, extra_evidence, say,
        author_model,
    )
    named = _failing_reviewer(
        results, it, gate, _converges(kind, fix), cfg, identifying, author_model
    )
    if item:
        outcome = _record_review(
            repo, log, cfg, st, it, item, gate, outcome,
            called_from=called_from, gates=gates, say=say, best=identifying, named=named, results=results, keeps=keeps, extra_evidence=extra_evidence,
            scope=(taken, kind, done, forced, how, len(diff), snapshot),
        )  # fmt: skip

    data: dict[str, Any] = {
        "id": item,
        "gate": gate,
        "outcome": outcome,
        "how": how,
        "reviewer": named.reviewer,
        "family": named.family,
        "findings": _finding_rows(results),
        "text": "\n".join(transcript),
    }
    exit_code = {
        RV.REVIEWED: O.OK,
        RV.PARTIAL: O.REFUSED,
        RV.UNAVAILABLE: O.NOTHING,
        RV.ERROR: O.FAIL,
    }[identifying.status]
    if exit_code == O.OK:
        return O.ok("review", **data)
    return O.Outcome(
        kind="review",
        data=data,
        exit=exit_code,
        reason=identifying.reason or f"{len(identifying.findings)} finding(s)",
    )
