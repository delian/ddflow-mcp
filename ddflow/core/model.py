"""Domain model and the fold — the deterministic projection of the event log.

``fold(events) -> State`` is a pure function. Same events in, same state out, on any
machine, in any process, at any time. Every rule in this module is therefore testable
without touching a disk, and ``ddflow rebuild`` is just ``fold`` over everything.

The hierarchy is deliberately two levels plus a grouping:

    Plan  ->  Phase  ->  Task

A **Phase** is a unit of *review* — it has its own research, its own whole-phase test
pass, its own live smoke run, and it merges as a coherent feature. A **Task** is a unit
of *execution* — one agent, one worktree, one pipeline, one merge. Both carry
``needs`` (dependencies), so a task may depend on a task in another phase and a phase
may depend on another phase. Anything deeper than this (epics, sub-sub-tasks) is
representable by making a phase's task depend on another's, and the extra level costs
more in bookkeeping than it buys.
"""

from __future__ import annotations

from collections.abc import Callable

from .events import (
    OLDER_MARK,
    SCHEMA_VERSION,
    SEEN_KIND,
    SKEW_OVERRIDDEN_KIND,
    UPGRADE_APPLIED_KIND,
    Event,
    changelog_of,  # noqa: F401  -- re-exported: model.py imported it before the split
    version_key,  # noqa: F401  -- re-exported: model.py imported it before the split
)
from .handlers._common import (  # noqa: F401
    _item,
)
from .handlers.approvals import APPROVAL_HANDLERS
from .handlers.defs import DEF_HANDLERS
from .handlers.exports import (
    _h_export_acknowledged,
    _h_export_disabled,
    _h_export_enabled,
)
from .handlers.flow import (  # noqa: F401
    _PR_FIELDS,
    _h_back_merge_recorded,
    _h_deploy_recorded,
    _h_flow_chosen,
    _h_port_applied,
    _h_pr_changes_requested,
    _h_pr_closed,
    _h_pr_merged,
    _h_pr_opened,
    _h_pr_synced,
    _h_release_closed,
    _h_release_opened,
    _h_release_tagged,
    _h_worktree_adopted,
    _h_worktree_created,
    _h_worktree_merged,
    _h_worktree_removed,
    _pr,
)
from .handlers.gates import (  # noqa: F401
    _count_recording,
    _h_cadence,
    _h_gate,
    _h_gate_out_of_order,
    _h_review_triaged,
    _h_reviewer_approved,
    _h_reviewer_configured,
)
from .handlers.items import (  # noqa: F401
    _apply_definition,
    _definition,
    _h_added,
    _h_removed,
    _h_reopened,
    _h_state,
    _h_unblocked,
    _h_updated,
    _safe_parent,
)
from .handlers.jobs import (  # noqa: F401
    SCHEDULE_FIELDS,
    TRIGGER_FIRES_KEPT,
    TRIGGER_RUNS_KEPT,
    TRIGGER_SUPPRESSIONS_KEPT,
    _h_external,
    _h_job_ended,
    _h_job_started,
    _h_schedule_defined,
    _h_schedule_removed,
    _h_schedule_updated,
    _h_trigger_evaluated,
    _h_trigger_fired,
    _h_trigger_suppressed,
    _schedule_with,
)
from .handlers.knowledge import (
    _h_bug_fixed,
    _h_bug_found,
    _h_bug_invalid,
    _h_bug_reopened,
    _h_bug_reported_upstream,
    _h_decision,
    _h_decision_superseded,
    _h_lesson,
    _h_memory,
    _h_memory_forgotten,
    _h_research,
)
from .handlers.leases import (  # noqa: F401
    _displace,
    _h_lease_acquired,
    _h_lease_gone,
    _h_lease_renewed,
    _h_resolved,
    _hold,
    _join,
    _key,
    _known,
    _late_renewal,
    _redisplay,
    _released_claim,
    _widen,
    _withdraw_claim,
)
from .handlers.links import (  # noqa: F401
    _h_link_recorded,
    _h_record_extended,
    _link,
    _linking,
    _links,
    link_targets,
)
from .handlers.sessions import (  # noqa: F401
    CI_RESULTS_KEPT,
    _h_ci_result,
    _h_ddflow_seen,
    _h_session_ended,
    _h_session_note,
    _h_session_prompt,
    _h_session_started,
    _h_skew_overridden,
    _h_upgrade_applied,
    _session,
)

# The records live in `records.py` and the handlers in `handlers/`; every name is re-exported
# here, so `from ddflow.core.model import State, Item, fold, HANDLERS, ...` keeps working.
from .records import (  # noqa: F401
    ABANDONED,
    ADD_RELATIONS,
    BLOCKED,
    DEFAULT_LEASE_TTL_S,
    DONE,
    GATE_OUTCOMES,
    LINK_RELATIONS,
    OPEN,
    OUTCOME_MARK,
    REVIEW,
    RUNNING,
    Bug,
    Decision,
    FoldProblem,
    GateRecord,
    Item,
    Job,
    Lease,
    Lesson,
    Memory,
    PullRequest,
    RecordLinks,
    Release,
    ResearchNote,
    Schedule,
    Session,
    State,
    _claim,
    _clashing,
    _overlaps,
    _span,
)
from .upcasters import NewerPayload, upcast

# ---------------------------------------------------------------------------------
# Event handlers.
#
# One function per event kind, and ``HANDLERS`` is the ONLY declaration of the
# vocabulary -- ``events.KINDS`` is derived from it. The alternative, a long if/elif
# ladder beside a separately-maintained set of kind strings, makes the vocabulary and
# its interpretation two lists that nothing forces to agree: a kind can be declared and
# never handled (folds to "nothing happened") or handled and never declared (rejected
# at append time). Deriving one from the other removes the disagreement structurally.
# ---------------------------------------------------------------------------------


HANDLERS: dict[str, Callable[[State, Event], None]] = {
    "phase.added": _linking(lambda st, ev: _h_added(st, ev, "phase")),
    "task.added": _linking(lambda st, ev: _h_added(st, ev, "task")),
    "phase.updated": lambda st, ev: _h_updated(st, ev, "phase"),
    "task.updated": lambda st, ev: _h_updated(st, ev, "task"),
    "phase.removed": lambda st, ev: _h_removed(st, ev, "phase"),
    "task.removed": lambda st, ev: _h_removed(st, ev, "task"),
    "lease.acquired": _h_lease_acquired,
    "lease.renewed": _h_lease_renewed,
    "lease.released": _h_lease_gone,
    "lease.expired": _h_lease_gone,
    "item.started": _h_state(RUNNING),
    "item.blocked": _h_state(BLOCKED),
    "item.unblocked": _h_unblocked,
    "item.resolved": _h_resolved,
    "item.completed": _h_state(DONE),
    "item.reopened": _h_reopened,
    "item.abandoned": _h_state(ABANDONED),
    **{f"gate.{o}": _h_gate(o) for o in ("started", *GATE_OUTCOMES)},
    "review.triaged": _h_review_triaged,
    "worktree.created": _h_worktree_created,
    # NOT the same handler. The two kinds were folded identically on the reasoning that
    # "the item is bound to a path and a branch either way" -- which threw away the one
    # bit that matters: whether ddflow made the tree. `remove_on_merge` then deleted the
    # agent's own worktree, the failure the adoption commit called worse than the one it
    # was fixing and claimed to prevent. A distinction kept only in the LOG is a
    # distinction the projection cannot act on.
    "worktree.adopted": _h_worktree_adopted,
    "worktree.merged": _h_worktree_merged,
    "worktree.removed": _h_worktree_removed,
    "pr.opened": _h_pr_opened,
    "pr.synced": _h_pr_synced,
    "pr.changes_requested": _h_pr_changes_requested,
    "pr.merged": _h_pr_merged,
    "pr.closed": _h_pr_closed,
    "port.applied": _h_port_applied,
    "flow.chosen": _h_flow_chosen,
    "backmerge.recorded": _h_back_merge_recorded,
    "deploy.recorded": _h_deploy_recorded,
    "release.opened": _h_release_opened,
    "release.tagged": _h_release_tagged,
    "release.closed": _h_release_closed,
    "bug.found": _linking(_h_bug_found),
    "bug.fixed": _h_bug_fixed,
    "bug.invalid": _h_bug_invalid,
    "bug.reopened": _h_bug_reopened,  # first writer: api/bug_reopen.py
    "bug.reported_upstream": _h_bug_reported_upstream,
    "lesson.recorded": _linking(_h_lesson),
    "research.recorded": _linking(_h_research),
    "decision.recorded": _linking(_h_decision),
    "decision.superseded": _h_decision_superseded,
    "memory.recorded": _linking(_h_memory),
    "job.started": _h_job_started,
    "external.observed": _h_external,
    "job.ended": _h_job_ended,
    "memory.forgotten": _h_memory_forgotten,
    "session.started": _h_session_started,
    "session.prompt": _h_session_prompt,
    "ci.result": _h_ci_result,
    "session.note": _h_session_note,
    "session.ended": _h_session_ended,
    "gate.out_of_order": _h_gate_out_of_order,
    "cadence.ran": _h_cadence,
    # first writer: api/schedule.py (define / update / remove)
    "schedule.defined": _h_schedule_defined,
    "schedule.updated": _h_schedule_updated,
    "schedule.removed": _h_schedule_removed,
    # first writer: api/schedule.py trigger_evaluate
    "trigger.evaluated": _h_trigger_evaluated,
    "trigger.fired": _h_trigger_fired,
    "trigger.suppressed": _h_trigger_suppressed,
    "reviewer.configured": _h_reviewer_configured,
    "reviewer.approved": _h_reviewer_approved,
    # first writer: services/approval.py grant / use
    **APPROVAL_HANDLERS,
    "record.extended": _h_record_extended,
    "link.recorded": _h_link_recorded,
    "export.enabled": _h_export_enabled,
    "export.disabled": _h_export_disabled,
    "export.acknowledged": _h_export_acknowledged,
    # first writer: api/defs.py (record / update / retire / supersede / merge)
    **DEF_HANDLERS,
    SEEN_KIND: _h_ddflow_seen,
    SKEW_OVERRIDDEN_KIND: _h_skew_overridden,
    UPGRADE_APPLIED_KIND: _h_upgrade_applied,
}


def known_kinds() -> frozenset[str]:
    """The event vocabulary, DERIVED from `HANDLERS`.

    Declaring it separately would make the vocabulary and its interpretation two lists that
    nothing forces to agree — a kind could be declared and never handled (folding silently
    to "nothing happened"), or handled and never declared (rejected at append time).

    Lives HERE, next to the handlers it derives from, rather than in `events`. It was in
    `events` with a lazy `from .model import HANDLERS`, which made the two a mutually
    importing pair held apart by one deferred import; `model` imports `Event` eagerly
    because it is in every signature, so one eager edge already existed and a second would
    have been an ImportError at startup. Its only consumer is `infra/log.py`'s append-time
    check, and `infra` may import `core` — so the derivation moves to the owner of the
    data and the cycle is gone rather than balanced (B127).
    """
    return frozenset(HANDLERS)


_SESSION_TEXT = ("session.prompt", "session.note")


def _problem(st: State, ev: Event, exc: Exception) -> None:
    st.fold_problems.append(
        FoldProblem(
            event=ev.id,
            kind=ev.kind,
            lamport=ev.lamport,
            agent=ev.agent,
            error=f"{type(exc).__name__}: {exc}",
        )
    )


def fold(events: list[Event], *, strict: bool = True) -> State:
    """Replay events into state. Pure; no I/O; deterministic.

    Each event is first brought to the current shape of its kind (`upcasters.upcast`).

    ``strict`` raises on an unknown kind, a payload newer than this code, and an event its
    handler cannot apply. Non-strict is for reading a log written by a NEWER ddflow than
    this one, where forward compatibility beats correctness of the unknown part -- but it
    counts what it skipped (`skipped_kinds`) and records what it could not apply
    (`fold_problems`), which doctor names.
    """
    st = State()
    # An adopted orphan lives on as the copy under its session; the id-less original
    # stays in the append-only log but is not folded, or its text would appear twice.
    adopted = {
        ev.data["adopted_from"]
        for ev in events
        if ev.kind in _SESSION_TEXT and isinstance(ev.data, dict) and ev.data.get("adopted_from")
    }
    for ev in events:
        st.event_count += 1
        st.last_lamport = max(st.last_lamport, ev.lamport)
        if ev.kind in _SESSION_TEXT and ev.id in adopted:
            continue
        if ev.schema > SCHEMA_VERSION:
            # Written by a ddflow whose event shape this code does not know: refused when
            # strict, like an unknown kind, else counted like a skipped kind so the same
            # "run the newer ddflow" advice applies. Never guessed at.
            if strict:
                raise ValueError(
                    f"event {ev.kind!r} at lamport {ev.lamport} has schema {ev.schema}; "
                    f"this code knows {SCHEMA_VERSION}"
                )
            key = f"{ev.kind} (schema {ev.schema})"
            st.skipped_kinds[key] = st.skipped_kinds.get(key, 0) + 1
            continue
        try:
            cur = upcast(ev)  # the current shape of its kind; the same object at version 1
        except NewerPayload:
            # A payload shape from a NEWER ddflow: preserved in the log, counted, never
            # guessed at -- the same handling and advice as an unknown kind (D-compat 2).
            if strict:
                raise
            key = f"{ev.kind} (payload v{ev.data.get('v')})"
            st.skipped_kinds[key] = st.skipped_kinds.get(key, 0) + 1
            continue
        except Exception as exc:
            if strict:
                raise
            _problem(st, ev, exc)
            continue
        # Provenance the LOG added to the payload, not part of the kind's shape: read from
        # the event as written, so an upcaster that rebuilds the payload cannot drop it.
        older = ev.data.get(OLDER_MARK)
        if older:
            st.older_version_events[str(older)] = st.older_version_events.get(str(older), 0) + 1
        handler = HANDLERS.get(cur.kind)
        if handler is None:
            if strict:
                raise ValueError(f"unknown event kind {cur.kind!r} at lamport {cur.lamport}")
            st.skipped_kinds[cur.kind] = st.skipped_kinds.get(cur.kind, 0) + 1
            continue
        if strict:
            handler(st, cur)
            continue
        try:
            handler(st, cur)
        except Exception as exc:
            # One malformed event no longer aborts the fold and hides every event after it
            # (B-uni-compat-events): it is recorded, `doctor` names it, and the fold goes on.
            _problem(st, cur, exc)
    return st
