"""One gate's review: refusals, the recording of its outcome, and per-item naming."""

from __future__ import annotations

import functools
import inspect
import time
from pathlib import Path
from typing import Any

from ...core import clock
from ...core import outcome as O
from ...services import gates as GD
from ...services import identity as ID
from ...services.completion import changed_paths
from ...services.gates import measured as GR
from ...services.gates import running as RUN
from ...services.gates.rounds import delta_rounds as _delta_rounds
from .._base import _load
from ..gate_guard import closed_item_refusal, wrong_tree_refusal
from .delta import _DELTA_CUT_HOW
from .findings import (
    _DELTA_KEYS,
    _hold,
    _link_refuted,
    _merge_delta,
    _say_triage_scope,
    _track_rerun_raised,
)
from .records import (
    _prior_evidence,
    _reason_of,
    _record,
    _reviewed_findings,
    _reviewer_families_of,
    _round_evidence,
)
from .reviewers import _begin_run, active_files


def _order_refusal(
    log, cfg, st, item: str, gate: str, say, defs=None, repo=None
) -> O.Outcome | None:
    """The pipeline-order check, asked BEFORE the review is paid for (one run before
    `implement` reviews an empty diff): a refusal under `block`, else a note said."""
    if not item:
        return None
    it = st.items.get(item)
    diff = (lambda: changed_paths(repo, it)) if repo is not None and it else None
    order = GR.check_order(log, cfg, st, item, gate, recording=True, defs=defs, diff=diff)
    if order.refusal:
        return O.refused(
            "review",
            order.refusal,
            id=item,
            gate=gate,
            outcome="",
            findings=[],
            how="",
            text=order.refusal,
        )
    if note := order.note(gate, cfg.gates.enforce_order):
        say(note)
    return None


def _log_started(log, it, item: str, gate: str) -> None:
    """The review's start, so reviewer latency (started -> outcome) is derivable (B7ed5137d45).
    Only for a real item: a diff reviewed with no item has no gate to start."""
    if it is not None:
        log.append("gate.started", item, {"gate": gate})


#: The synthetic key of the per-item review lock: it names no gate, so it never collides
#: with a command gate's started-marker, and a dead one is skipped by the dead-run sweep.
_REVIEW_LOCK_GATE = "review"


def _claim_or_refuse_twin(repo: Path, item: str, sha: str) -> tuple[Any, O.Outcome | None]:
    """(marker, refusal): claim the per-ITEM review lock, or refuse a second review of the
    same item while one is live (B8664a1e0c6). Its record is per item, not per gate or per
    commit, so a second `ddflow review` of the item -- any gate, any commit -- races the
    first for the same gate records, and the first to take the lock reviews while the second
    sees its marker and refuses. Held across the WHOLE call (all of the item's gates), not
    per gate. The lock uses a synthetic key that is no gate, so it never collides with a
    command gate's marker and a dead one is skipped by the sweep. (None, None) when there is
    no item (nothing is recorded, nothing to race); a marker the filesystem could not write
    is refused too, because without it a concurrent review could not be refused."""
    if not item:
        return None, None
    marker, twin = RUN.claim_started(repo, item, _REVIEW_LOCK_GATE, sha, same_commit=False)
    if twin is not None:
        return None, O.nothing(
            "review",
            f"this item's gates are already being reviewed (pid {twin.pid}, since "
            f"{twin.at}); wait for it -- a second review would only race it for the one "
            f"outcome and pay the reviewer twice. A review can start once the first ends.",
            id=item,
        )
    if marker is None:
        return None, O.nothing(
            "review",
            f"the review could not write its started-marker for {item}; refusing rather "
            f"than run without the concurrent-review lock",
            id=item,
        )
    return marker, None


def _unknown_item(repo: Path, item: str, agent: str, gate: str) -> O.Outcome | None:
    """A refusal for an `item` the queue does not hold, else None.

    Checked before anything is recorded: the fold creates an item for an unknown subject, so
    a typo'd id would otherwise gain a phantom task carrying the review's outcome (B8d9e8108f8).
    """
    if not item:
        return None
    it = _load(repo, agent)[2].items.get(item)
    if it is not None and not it.removed:
        return None
    gone = " (it was removed from the queue)" if it is not None else ""
    return O.failed("review", f"no such item {item!r}{gone}", id=item, gate=gate)


def _misplaced(repo, item, agent, gate, branch, commit, called_from, force, reason):
    """A refusal (B8b2dafcbf3) for a review of a finished item, or from the wrong tree."""
    if not item:
        return None
    it = _load(repo, agent)[2].items.get(item)
    # `--commit` is the after-merge review: recorded against the item, done or not.
    why, _ = (
        ("", "")
        if commit
        else closed_item_refusal(it, force=force, reason=reason, what=f"reviewing {gate}")
    )
    if not why and not (branch or commit):
        why = wrong_tree_refusal(repo, _load(repo, agent)[2], it, called_from)
    return O.refused("review", why, id=item, gate=gate) if why else None


def _ends_on_signal(fn):
    """`fn` (a `review`) with SIGTERM/SIGHUP -- the usual `timeout` -- turned into a said and
    recorded `unavailable` (`_terminated`) instead of a silent death (Bed9d75dd1c)."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        bound = sig.bind(*a, **kw)
        bound.apply_defaults()
        started_at = clock.now_iso()
        _begin_run()  # forget prior runs BEFORE the guard can checkpoint their files (B982d306cc8)
        try:
            with RUN.terminating():
                return fn(*a, **kw)
        except RUN.RunTerminated as stop:
            return _terminated(dict(bound.arguments), stop.signame, started_at)

    return wrapper


_NOTHING_REVIEWED = "nothing was reviewed, which is not a pass"


def _terminated_gate(
    repo, log, cfg, st, item: str, gate: str, signame: str, called_from, gates
) -> str:
    """One gate's record for a review cut short by ``signame``; returns the reason recorded.

    Chunks already checkpointed (bug B982d306cc8) become a `partial` record that keeps their
    findings -- a killed background shell loses no reviewer work; none become `unavailable`."""
    files = active_files(item, gate)
    reviewed = sorted({c for f in files for c, _r in f.chunks()})
    if not reviewed:
        reason = (
            f"the review was terminated by {signame} before a reviewer finished: "
            f"{_NOTHING_REVIEWED}"
        )
        _record(
            repo, log, cfg, st, item, gate, "unavailable", called_from, gates,
            reason=reason, evidence={"review_kind": "delta"},
        )  # fmt: skip
        return reason
    evidence: dict[str, Any] = {
        "review_kind": "delta",
        "terminated_by": signame,
        "status": "PARTIAL",
        "reviewed": reviewed,
        "chunk_findings": [fd for f in files for fd in f.findings()],
    }
    for f in files:
        evidence.update(f.evidence())
    reason = (
        f"the review was terminated by {signame} mid-round after {len(reviewed)} chunk(s) "
        f"were reviewed ({', '.join(str(c) for c in reviewed)}); those findings are "
        f"checkpointed, the remaining chunks were not reviewed, which is not a pass"
    )
    _record(
        repo, log, cfg, st, item, gate, "partial", called_from, gates,
        reason=reason, evidence=evidence,
    )  # fmt: skip
    return reason


def _terminated(args: dict[str, Any], signame: str, started_at: str) -> O.Outcome:
    """A review ended by SIGTERM/SIGHUP (the usual `timeout`) before its reviewers finished:
    said, and recorded per gate asked. See `_terminated_gate` for what is recorded, and
    `_keep_reviewed_round` for why a delta never over a round this run already recorded
    (Bed9d75dd1c)."""
    repo, item = args["repo"], args["item"]
    gate_names = [g.strip() for g in args["gate"].split(",") if g.strip()]
    fallback = (
        f"the review was terminated by {signame} before a reviewer finished: {_NOTHING_REVIEWED}"
    )
    reasons: list[str] = []
    kept: list[str] = []
    log, cfg, st = _load(repo, args["agent"])
    it = st.items.get(item) if item else None
    gates = GD.load_gates(repo, cfg)
    if it is not None and gate_names:  # an id no item has is never recorded (B8d9e8108f8)
        for g in gate_names:
            rec = it.gates.get(g) if it else None
            if rec and rec.outcome and clock.epoch(rec.at) >= clock.epoch(started_at):
                kept.append(f"{g} = {rec.outcome}")  # this run already recorded it
                continue
            reasons.append(
                _terminated_gate(repo, log, cfg, st, item, g, signame, args["called_from"], gates)
            )
    reason = "; ".join(reasons) if reasons else fallback
    if kept:
        reason += f" (already recorded by this run: {', '.join(kept)})"
    return O.nothing(
        "review",
        reason,
        id=item,
        gate=args["gate"],
        outcome="unavailable",
        findings=[],
        text=reason,
    )


def _record_review(  # noqa: PLR0913 -- everything the run knew when it finished
    repo, log, cfg, st, it, item: str, gate: str, outcome: str,
    *, called_from, gates, say, best, results, keeps, extra_evidence, scope, named=None,
) -> str:  # fmt: skip
    """Write the review's outcome against ``it`` and say so; the outcome recorded (a pass
    over untriaged findings of an earlier round is held, see `_hold`).

    ``best`` is the identifying pass -- its status/coverage/kept reply -- while ``named`` is
    the reviewer the record NAMES: for a failed gate, the one whose finding blocked it (bug
    B3113b1f522), else ``best``. Naming the failing reviewer must not re-purpose a PARTIAL
    reviewer as the identifying pass (which would misstate the round's coverage)."""
    named = best if named is None else named
    taken, kind, done, forced, how, diff_chars, snapshot = scope
    evidence = {
        **best.evidence(),
        "diff_source": how,
        "diff_chars": diff_chars,
        **_round_evidence(taken, kind, done, best.status, forced, _delta_rounds(it, gate)),
        **(keeps[best.reviewer].evidence() if best.reviewer in keeps else {}),
        "reviewer_families": _reviewer_families_of(results),
        **(extra_evidence or {}),
        **snapshot,
    }
    if named is not best:
        # Attribution only: the identifying pass's status/coverage stay `best`'s.
        evidence["reviewer"] = named.reviewer
        evidence["model"] = named.model
        evidence["family"] = named.family
    reviewed = (
        _reviewed_findings(results) if results is not None else best.evidence()["chunk_findings"]
    )
    evidence["chunk_findings"] = reviewed
    evidence["findings"] = len(reviewed)
    evidence["titles"] = [f["title"] for f in reviewed[:10]]
    if evidence.get("retry_of_partial"):
        evidence["rounds"] = done  # the retry of a partial round is no round of its own
        evidence.pop("round", None)
    if kind == "chunk" and it is not None:
        # A re-run of part of a DELTA round stays that delta's record: the next --chunk
        # needs its start and which findings are its own (B00e6b5a540).
        prior = _prior_evidence(it.gates.get(gate))
        # `delta_from` only when this re-run WAS cut from it, not from the user's own ref
        keys = [k for k in _DELTA_KEYS if k != "delta_from" or how.startswith(_DELTA_CUT_HOW)]
        evidence.update({k: prior[k] for k in keys if k in prior and k not in evidence})
        _track_rerun_raised(evidence, prior, best)
    held = _merge_delta(log, it, gate, kind, best.status, evidence, say)
    clean_pass = outcome == "passed"
    linked = _link_refuted(log, it, gate, reviewed)
    if linked:
        held = max(0, held - len(linked))
    outcome, reason = _hold(outcome, _reason_of(best, reviewed), held, say)
    if clean_pass and outcome == "failed":
        evidence["held_pass"] = True
    _record(
        repo, log, cfg, st, item, gate, outcome, called_from, gates,
        reason=reason, evidence=evidence, by=named.model,
    )  # fmt: skip
    if linked:
        say(
            f"→ auto-linked finding(s) {', '.join(linked)} of {item}.{gate} to the item's "
            f"refuted triage (bug B74d5c39e6e): the claim was already refuted, its probe "
            "stands on this gate too"
        )
    say(f"\nrecorded {item}.{gate} = {outcome} (reviewer {named.reviewer}, family {named.family})")
    _say_triage_scope(say, results, named)
    return outcome


def _name_the_item(
    repo: Path, cfg, st, item: str, gate: str, agent: str, say, extra: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Say, loudly, WHICH item this review is recorded against and whose it is
    (DX-review-item-from-cwd). A review launched from a backgrounded shell chain once
    recorded a failed gate on another agent's item; nothing said whose it was.

    No item named: the working tree is reviewed and NOTHING is recorded, said as such. An
    item whose live lease another agent holds: a WARNING and a line in the gate's evidence
    (not a refusal: agents of one clone may review each other's items, D-mu-bugfix-answers).
    """
    if not item:
        say(
            f"→ no item named: reviewing the working tree, and recording nothing for {gate}. "
            "Name the item (`ddflow review <id>`) to record the outcome."
        )
        return extra
    it = st.items.get(item)
    lease = it.lease if it else None
    if lease is None or not lease.live(time.time(), cfg.lease.grace_s):
        if it is not None and it.state not in ("done", "abandoned"):
            say(
                f"→ note: {item} has no live lease: this review is recorded against it all the same."
            )
            return {**(extra or {}), "reviewed_unleased": True}
        return extra
    me = ID.resolve(repo, cfg, agent).id
    if me == lease.holder:
        return extra
    say(
        f"→ WARNING: {item} is leased to {lease.holder}, and you are {me}: this review is "
        f"recorded against {lease.holder}'s item. If that is not what you meant, it was the "
        f"wrong item (or the wrong --agent): ask {lease.holder} to re-run the gate."
    )
    return {**(extra or {}), "reviewed_for_holder": f"{me} reviewed {lease.holder}'s {item}"}


def _say_selection(selected, say) -> None:
    """What the pool and route stages decided, said: a skipped candidate is never silent."""
    for line in selected.notes:
        say(f"→ {line}")
    if selected.agent:
        say(f"→ route: {selected.agent}")


def _early_refusal(flags, log, cfg, st, item: str, gate: str, say, defs=None, repo=None):
    """The first reason to refuse before any work: contradicting flags, then the order. (Not
    `a or b`: a refusal is falsy.)"""
    conflict = _flag_conflict(*flags, item, gate)
    return (
        conflict
        if conflict is not None
        else _order_refusal(log, cfg, st, item, gate, say, defs, repo)
    )


def _intent_missing(item: str, gate: str, how: str) -> O.Outcome:
    return O.failed(
        "review",
        "--intent is required: the reviewer flags where the diff and the stated "
        "intent disagree, so without it there is nothing to disagree with.",
        id=item,
        gate=gate,
        outcome="",
        findings=[],
        how=how,
        text="",
    )


def _flag_conflict(full: bool, force: bool, delta: bool, item: str, gate: str) -> O.Outcome | None:
    """`--full` against `--delta`: a full round and a delta contradict each other. `--force`
    is past the round budget, not a kind of round, so a forced round may be a delta
    (Ba93aec8448)."""
    if not (full and delta):
        return None
    return O.failed(
        "review",
        "--full and --delta contradict each other: --full is a full round, --delta "
        "reviews only what changed since the reviewed head.",
        id=item,
        gate=gate,
        outcome="",
        findings=[],
        text="",
    )
