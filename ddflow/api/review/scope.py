"""What a review run scopes: rounds, budget, justification, and delta rechecking."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import blobref as B
from ...core import outcome as O
from ...infra import worktree as W
from ...services import grants as GNT
from ...services.gates import reviewed_paths as RP
from ...services.gates.rounds import full_rounds as _full_rounds
from ...services.gates.rounds import kept_copy as _kept_copy
from ...services.gates.rounds import rounds_used as _rounds_used
from ..gates import item_tree
from ..review_findings import _rows as _numbered_findings
from ..review_supersede import superseded_digests
from .delta import _NOTHING_CHANGED, _delta_diff, _head_of, _kind, _last_head, _pick_diff
from .findings import _SETTLED
from .records import _last_review, _prior_evidence, _review_runs
from .triaging import _moved_paths, _name_paths, _rerun_scope


def _resume_of_terminated(it, gate: str, revs: list, diff: str, chunk_max_tokens: int = 0):
    """(reviewer, the record, the chunks) to resume a review a signal cut short, or None.

    A review killed mid-run leaves a PARTIAL record (bug B982d306cc8) whose evidence carries
    its `reviewed` chunks and, since DX-review-chunk-checkpoint, the cut's `chunks_total`,
    `diff_sha` and `wanted` subset. This resumes from the next unreviewed chunk of the SAME
    cut instead of paying every chunk twice; `_rerun_scope` does the validation (the diff,
    chunk size and reviewer must all match, or a resume would merge chunk numbers that mean
    something else). A resume never widens the original scope: only chunks the killed run was
    asked to review (`wanted`) are eligible, so a `--chunk 2` review does not resume 1,3,4,5.

    Lives here (scope, not triaging) so the resume does not need a file another lane may hold.
    """
    if it is None:
        return None
    prior = _prior_evidence(it.gates.get(gate))
    if prior.get("terminated_by") is None or prior.get("status") != "PARTIAL":
        return None
    total = prior.get("chunks_total")
    if not isinstance(total, int) or total < 1:
        return None
    reviewed = {int(c) for c in prior.get("reviewed", [])}
    wanted = prior["wanted"] if "wanted" in prior else list(range(1, total + 1))
    missing = [n for n in wanted if n not in reviewed]
    if not missing:
        return None
    ok = _rerun_scope(it, gate, revs, diff, missing, chunk_max_tokens)
    return None if isinstance(ok, str) else ok


def _budget_refusal(item: str, gate: str, done: int, cap: int) -> str:
    return (
        f"{item}.{gate} has had {done} review rounds, full and delta ([review].max_rounds = "
        f"{cap}). Later rounds find fewer defects than earlier ones, so instead:\n"
        f"  ddflow review triage {item} --gate {gate} --finding N --refuted|--confirmed "
        '--probe "..."    settle each remaining finding: refute it with the run that shows it '
        "false, or confirm it with the test that now passes (always allowed)\n"
        "  the triage that settles the last finding records the gate passed on refutation "
        "(flagged); one you cannot settle goes to the operator\n"
        f'  ddflow review {item} --gate {gate} --force --reason "..."    one more round, '
        "recorded\n"
        "To change the budget: ddflow config --set review.max_rounds N [--local] "
        '(0 = unlimited), or [review].on_exceed = "warn".'
    )


def _superseded(repo, cfg, it, a) -> tuple[set[str], set[str]]:
    """(newly, earlier): digests of the gate's recorded findings whose cited lines the
    commits since the reviewed head rewrote, and those an earlier round already marked.
    Nothing new without a usable head (none recorded, or no longer an ancestor)."""
    last = _last_review(it, a["gate"])
    found = last.get("chunk_findings")
    head = str(last.get("reviewed_head") or "")
    if not head or not found or isinstance(found, B.Unavailable):
        return set(), set()
    old = {str(d) for d in last.get("superseded") or []}
    tip = _head_of(repo, it, a["branch"], "")
    if not tip or not W.git(repo, "merge-base", "--is-ancestor", head, tip).ok:
        return set(), old
    try:
        diff = _delta_diff(repo, it, a["branch"], head, cfg.worktree.base_ref or "")
    except RuntimeError:
        return set(), old
    return superseded_digests(list(found), diff) - old, old


def _untriaged_numbers(it, gate: str, last: dict) -> list[int] | None:
    """The numbers (as `review` printed them) of the last round's findings with no settled
    verdict; None when the record cannot be read (then it counts as open)."""
    found = last.get("chunk_findings")
    if isinstance(found, B.Unavailable) or not isinstance(found, list):
        return None if found is not None else []
    mine = it.triage.get(gate, {})
    return [
        n
        for n, f in enumerate(found, 1)
        if mine.get(str(f.get("digest")), {}).get("verdict") not in _SETTLED
    ]


def _justification(repo: Path, cfg, st, it, a: dict[str, Any]) -> tuple[bool, str]:
    """(is a round justified, why) for a call to review ``a['item']``'s ``a['gate']``
    (``[review].require_new_work``, decision D-review-rounds-4): a round after a recorded one
    needs new work -- the item's reviewed paths differ from that round's `reviewed_paths`, or
    a finding of it is untriaged. Anything it cannot tell is justified: only a proven
    repeat is refused."""
    if not (it and a["item"]):
        return True, ""
    if not cfg.review.require_new_work:
        return True, "not required ([review].require_new_work = false)"
    if a["chunks"] or a["commit"] or a["base"] or a["delta"] or a["full"] or a["force"]:
        return True, "asked for explicitly" + _repeat_of_terminated(it, a["gate"])
    gate = a["gate"]
    last = _last_review(it, gate)
    if not last.get("reviewed_head"):
        return True, "first round"
    if str(last.get("status")) != "REVIEWED":
        return True, "the last round did not cover the whole diff"
    open_ = _untriaged_numbers(it, gate, last)
    if open_ is None:
        return True, "the last round's findings cannot be read"
    if open_:
        return True, "finding " + ", ".join(f"#{n}" for n in open_) + " untriaged"
    wt, _held = item_tree(repo, cfg, st, it, a["called_from"])
    moved = _moved_paths(repo, it, wt, last.get(RP.REVIEWED_PATHS_FIELD))
    if moved is None:
        return True, "the reviewed paths cannot be compared with the last round's"
    if moved:
        return True, f"reviewed paths changed ({_name_paths(moved)})"
    return False, "reviewed paths unchanged, all findings triaged"


def _repeat_of_terminated(it, gate: str) -> str:
    """' (repeats the review terminated by SIGTERM)' when the gate's latest recorded run was
    cut short by a signal, so an explicit re-review (`--force --reason`, `--full`, ...) does
    not look like a fresh round; '' otherwise. The terminated record's `terminated_by` is
    set by `_terminated_gate` (bug B-ddflow-review-force-duplicate-round)."""
    runs = _review_runs(it, gate)
    if not runs:
        return ""
    signame = str((runs[-1].evidence or {}).get("terminated_by") or "")
    return f" (repeats the review terminated by {signame})" if signame else ""


def _nothing_new(it, a: dict[str, Any], ok: bool, why: str) -> str:
    """Why no reviewer is called when the round is not justified (``ok`` False); none is
    counted."""
    item, gate = a["item"], a["gate"]
    used = _rounds_used(it, gate) if it and item else 0
    if ok:
        return ""
    return f"nothing new to review since round {used} ({why}); {item}.{gate} stays as recorded."


def _rounds_report(cfg, it, a: dict[str, Any], why: str) -> str:
    """The one-line report of a round that will run: rounds used counting it (a free fix
    round or a free retry of a partial round counts none), against the cap, and why it is
    justified."""
    free = a.get("fix") or a.get("partial_retry")
    used = _rounds_used(it, a["gate"]) + (0 if free else 1)
    cap = cfg.review.max_rounds
    note = (
        " (fix round: not counted)"
        if a.get("fix")
        else " (free retry of a partial round: not counted)"
        if a.get("partial_retry")
        else ""
    )
    return f"rounds used {used}/{cap if cap > 0 else 'unlimited'}{note}, justified: {why}"


def _mark_partial_retry(extra: dict | None, a: dict[str, Any]) -> dict | None:
    """``extra`` plus `retry_of_partial` for a round `_is_partial_retry` found free."""
    return {**(extra or {}), "retry_of_partial": True} if a.get("partial_retry") else extra


def _is_partial_retry(it, a: dict[str, Any], kind: str) -> bool:
    """Is this full round the one retry of a PARTIAL full round (a chunk came back off
    contract, empty or timed out)? The partial round was charged and delivered less than
    it was charged for; its retry is no new round -- once: a retry that is partial again
    is paid for (Bdfc7ed1299). A `--chunk` re-run of only the missing chunks is free too,
    but works only while the diff is unchanged (its numbers mean nothing in another diff);
    this whole-diff retry is what is left once the branch moved."""
    if kind != "full" or not (it and a["item"]) or a["force"]:
        return False
    last = next(
        (r for r in reversed(_review_runs(it, a["gate"])) if r.evidence.get("reviewed_head")), None
    )
    if last is None:
        return False
    ev = last.evidence
    return (
        ev.get("review_kind") == "full"
        and ev.get("status") == "PARTIAL"
        and bool(ev.get("unreviewed"))
        and not ev.get("retry_of_partial")
        and not _kept_copy(last)
    )


def _is_fix_round(cfg, it, a) -> bool:
    """A pure fix round (``[review].fix_round_free``): commits since the reviewed head
    addressed EVERY finding of the last round -- its lines were rewritten, or the author
    settled it -- and rewrote at least one cited line (else a review with no fix would be
    a free loop), so re-checking the fixes is no new round of review."""
    if not (cfg.review.fix_round_free and it and a["item"]):
        return False
    if a["chunks"] or a["commit"] or a["base"] or a["full"]:
        return False
    found = _last_review(it, a["gate"]).get("chunk_findings")
    if not found or isinstance(found, B.Unavailable) or not a["supers_new"]:
        return False
    mine = it.triage.get(a["gate"], {})
    return all(
        f.get("digest") in a["supers"]
        or mine.get(str(f.get("digest")), {}).get("verdict") in _SETTLED
        for f in found
    )


def _scope(repo, cfg, st, log, it, say, revs, *, locals_: dict[str, Any]):
    """(kind, rounds done, forced reason, diff, how, why refused): what this call reviews
    and whether the round budget lets it. `locals_` is review()'s own arguments."""
    a = locals_
    item, gate = a["item"], a["gate"]
    ok, why_ok = _justification(repo, cfg, st, it, a)
    if idle := _nothing_new(it, a, ok, why_ok):
        a["refusal_exit"] = O.NOTHING  # nothing to review is exit 2, a budget refusal 3
        return "full", 0, "", "", "", idle, None
    delta = _wants_delta(repo, cfg, it, a, say)
    new, old = _superseded(repo, cfg, it, a) if it and item and not a["chunks"] else (set(), set())
    a["supers"], a["supers_new"] = new | old, new
    a["fix"] = not delta and _is_fix_round(cfg, it, a)
    delta = delta or a["fix"]
    kind = _kind(repo, cfg, it, item, gate, a["chunks"], delta, a["commit"], a["base"])
    a["partial_retry"] = _is_partial_retry(it, a, kind)
    done = _full_rounds(it, gate) if item else 0
    forced, diff, how, why = "", "", "", ""
    if kind in ("full", "delta") and item and not a["fix"] and not a["partial_retry"]:
        # Delta rounds count too (D-gate-economy 2): a delta after the cap was the way
        # around it, and each one was another reviewer request at the same item.
        used = _rounds_used(it, gate)
        why, forced = _budget_on_grants(repo, cfg, st, it, a, used, say)
    if why:
        return kind, done, forced, diff, how, why, None
    diff, how, why = _pick_diff(repo, cfg, st, it, a, delta)
    # Reported only for a round that will really run and count: an unchanged delta is
    # refused below, and saying "rounds used N+1" for it read as the round it burned.
    if item and not _flagged_unchanged_delta(a, why):
        say(_rounds_report(cfg, it, a, why_ok))
    rerun = None
    if a["chunks"] and diff.strip():
        rerun = _rerun_scope(it, gate, revs, diff, a["chunks"], cfg.review.chunk_max_tokens)
        why, rerun = (rerun, None) if isinstance(rerun, str) else ("", rerun)
    if rerun is None and not why and it is not None and diff.strip():
        # A review cut short by a signal resumes from the next chunk: the SAME cut's unreviewed
        # chunks merge into the partial record the kill left (DX-review-chunk-checkpoint). Guarded
        # by `not why`: an explicit --chunk request that failed explains itself in `why`; a resume
        # must not paper over that refusal with a fresh chunk list (B-review-chunk-checkpoint).
        rerun = _resume_of_terminated(it, gate, revs, diff, cfg.review.chunk_max_tokens)
    return kind, done, forced, diff, how, why, rerun


def _flagged_unchanged_delta(a: dict[str, Any], why: str) -> bool:
    """Flagged when `why` is the delta's "nothing changed since the reviewed head" answer
    (`_delta_scope`): the head covers the tip, so no round runs or counts, and
    `_why_outcome` re-evaluates the gate instead of refusing (B10ad449deb)."""
    a["unchanged_delta"] = bool(why) and why.startswith(_NOTHING_CHANGED)
    return bool(a["unchanged_delta"])


def _budget_on_grants(repo, cfg, st, it, a: dict[str, Any], used: int, say) -> tuple[str, str]:
    """`_budget` for this call, with the operator grant (`services.grants.pick`) a round past
    the budget may cite. Says the item's grants for the gate; ``a["grant"]`` is the id the
    round ran on (set only when it did: a round the budget allows anyway spends none)."""
    item, gate = a["item"], a["gate"]
    for line in GNT.lines(st, item, gate):
        say(line)
    grant = None
    if 0 < cfg.review.max_rounds <= used:
        grant = GNT.pick(st, item, gate, a["force"], ctx=_grant_ctx(repo, it, gate, a))
    why, forced = _budget(cfg, item, gate, used, a["force"], a["reason"], say, grant)
    a["grant"] = grant.id if grant and forced else ""
    return why, forced


def _cite_grant(extra: dict[str, Any] | None, grant_id: str) -> dict[str, Any] | None:
    """``extra`` evidence naming the operator grant the round ran on (`services.grants`
    counts a grant's use from it)."""
    return {**(extra or {}), "budget_grant": grant_id} if grant_id else extra


def _grant_ctx(repo, it, gate: str, a: dict[str, Any]) -> GNT.Ctx:
    """The facts a standing operator grant's predicate reads (`services.grants.Ctx`)."""
    # No numbered findings on record (a clean or hand-recorded gate: `_rows` says why in text)
    # reads as "untriaged", so a standing grant never fires on what it cannot see: a clean
    # round has nothing left to triage and needs no extra round.
    rows = _numbered_findings(it, gate)
    return GNT.Ctx(
        untriaged=(
            sum(1 for r in rows if r["triage"] == "untriaged") if isinstance(rows, list) else 1
        ),
        reviewed_head=_last_head(it, gate),
        head=_head_of(repo, it, a["branch"], a["commit"]),
    )


def _wants_delta(repo, cfg, it, a, say) -> bool:
    """Is this call a delta recheck? `--delta` says so; with `[review].delta_default` (off
    unless a project turns it on, decision D-gate-economy 3) so does a plain review of a
    gate that already has a recorded one.

    `--full`, `--force` (past the budget: a full round unless `--delta` asks for a delta,
    Ba93aec8448), `--chunk`, `--commit` and `--base` are left
    as they are asked for. A delta is only sound from a head the branch still contains: one
    that is no ancestor of the tip (rebased, amended) would diff the rebase's own changes,
    so it falls back to a full round and says why -- as does, for the automatic delta only,
    a prior review that did not cover the whole diff (an explicit `--delta` is the author's
    own choice; the recorded `coverage` is kept beside the delta's).
    """
    explicit = bool(a["delta"])
    plain = not (a["chunks"] or a["commit"] or a["base"] or a["full"] or a["force"])
    if not it or not (explicit or (plain and cfg.review.delta_default)):
        return False
    item, gate = a["item"], a["gate"]
    last = _last_review(it, gate)
    head = str(last.get("reviewed_head") or "")
    if not head:
        return explicit  # --delta says "run the full review first"; a plain review just is one
    if str(last.get("status")) != "REVIEWED":
        if not explicit:
            say(
                f"full round: the last {gate} review of {item} was {str(last.get('status')).lower()}"
                ", so a delta from it would leave part of the diff unreviewed."
            )
            return False
    tip = _head_of(repo, it, a["branch"], "")
    if not tip or not W.git(repo, "merge-base", "--is-ancestor", head, tip).ok:
        say(
            f"full round: the reviewed head {head[:10]} is not an ancestor of the current "
            f"head {tip[:10] or '(unknown)'} (the branch was rebased or amended), so a delta "
            "from it would not be what changed."
        )
        return False
    if not explicit:
        say(
            f"[review].delta_default: {item}.{gate} has a recorded review, so only what "
            f"changed since {head[:10]} is reviewed (`--full` forces a full round)."
        )
    return True


def _budget(cfg, item, gate, done, force, reason, say, grant=None) -> tuple[str, str]:
    """(why this round is refused, "") or ("", the reason it was forced past the cap).
    ``done`` is every round used so far, full and delta (`_rounds_used`). ``grant`` is the
    operator grant (`services.grants.pick`) this round may cite: it stands in for the
    free-text reason, and a standing one needs no ``--force`` at all."""
    cap = cfg.review.max_rounds
    if cap <= 0 or done < cap:
        return "", ""
    if cfg.review.on_exceed == "warn":
        say(
            f"WARNING: {item}.{gate} has had {done} review rounds, past "
            f"[review].max_rounds = {cap}: prefer `review triage`."
        )
        return "", ""
    if grant is not None:
        say(
            f"round {done + 1} of {item}.{gate} runs past max_rounds = {cap} on {GNT.describe(grant)}."
        )
        note = f"; agent: {reason.strip()}" if reason.strip() else ""
        return "", f"operator grant {grant.id}: {grant.reason}{note}"
    if force and reason.strip():
        say(f"--force: round {done + 1} of {item}.{gate} runs past max_rounds = {cap}.")
        return "", reason.strip()
    if force:
        return '--force needs --reason "...": the exception is recorded.', ""
    return _budget_refusal(item, gate, done, cap), ""
