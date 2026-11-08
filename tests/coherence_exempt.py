"""The event kinds the add-time duplicate check does not cover, and why, for
`tests/test_coherence_coverage.py`: a table in its own file so that test stays under the file-size
limit. `scripts/event_kind.py` adds a new kind's row here.
"""

from __future__ import annotations

_STATE = "a state change of a record that already exists; it adds no text to compare"
_UPDATE = "changes or closes an existing record (same id); the add was checked"
_OBSERVED = "a machine observation or run record, not authored text"
_CONFIG = "who changed a setting or approved a tool; the setting itself lives in config"
#: Every other kind, and why it is not duplicate-checked. The gate records of an item
#: (`GATE_RECORDS`, state) are exempt by name below.
EXEMPT: dict[str, str] = {
    "merge.forced": "gate names and an approval stamp only; no claim about code to check",
    "token.revoked": "records a credential's end; its reason is the operator's note, not a checkable claim",
    "token.issued": "records who holds a credential; it carries no checkable project text",
    "probe.recorded": "a machine record of a conflict probe: refs, shas and paths, no authored text",
    "suggestion.expired": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.superseded": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.withdrawn": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.countered": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.rejected": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.applied": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.accepted": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.seen": "a state move carries no text that is compared for duplicates (services.handoff)",
    "suggestion.proposed": "a patch and its reason are compared by the handoff service, which owns the duplicate rule (services.handoff)",
    "phase.completed": "records the deliberate retire of a settled phase: no authored text beyond its evidence",
    "lane.released_for_gate": "a machine record of a release: ids and gate names, no authored text",
    "memory.redacted": "a redaction marker names the superseded event id and why; memory_add writes it, not an authored add to duplicate-check",
    **dict.fromkeys(
        (
            "lease.acquired",
            "lease.renewed",
            "lease.released",
            "lease.expired",
            "item.started",
            "item.blocked",
            "item.unblocked",
            "item.completed",
            "item.reopened",
            "item.reworked",
            "item.abandoned",
            "item.resolved",
            "phase.removed",
            "task.removed",
            "review.triaged",
            "worktree.created",
            "worktree.adopted",
            "worktree.merged",
            "worktree.removed",
            "pr.opened",
            "pr.synced",
            "pr.changes_requested",
            "pr.merged",
            "pr.closed",
            "port.applied",
            "backmerge.recorded",
            "deploy.recorded",
            "release.opened",
            "release.tagged",
            "release.closed",
            "session.started",
            "session.ended",
            "gate.out_of_order",
        ),
        _STATE,
    ),
    **dict.fromkeys(
        (
            "phase.updated",
            "task.updated",
            "bug.fixed",
            "bug.invalid",
            "bug.reopened",
            "bug.tagged",
            "bug.reported_upstream",
            "decision.superseded",
            "memory.forgotten",
            "def.updated",
            "def.retired",
            "def.superseded",
            "def.merged",
        ),
        _UPDATE,
    ),
    **dict.fromkeys(
        ("ci.result", "external.observed", "job.started", "job.ended", "cadence.ran"), _OBSERVED
    ),
    **dict.fromkeys(
        (
            "flow.chosen",
            "reviewer.configured",
            "reviewer.approved",
            "export.enabled",
            "export.disabled",
            "export.acknowledged",
            "ddflow.seen",
            "ddflow.capabilities",
            "skew.overridden",
            "upgrade.applied",
        ),
        _CONFIG,
    ),
    "session.prompt": "the operator's words, verbatim: never refused or merged (D-no-duplicates)",
    "session.note": "a journal entry of what happened, verbatim (D-no-duplicates)",
    **dict.fromkeys(
        ("schedule.defined", "schedule.updated", "schedule.removed"),
        "a scheduled job's definition: a document keyed by its id (defining an id again "
        "replaces it) and validated by api.schedule; the authoring surfaces that add a "
        "duplicate check are B-sched-author",
    ),
    **dict.fromkeys(
        ("trigger.evaluated", "trigger.fired", "trigger.suppressed"),
        "the trigger evaluator's own record of a run, a fire or a suppression: no authored text",
    ),
    **dict.fromkeys(
        ("approval.granted", "approval.used"),
        "a person's approval of a digest, or a single-use approval spent: no authored "
        "text to compare (services.approval)",
    ),
    "lease.waiting": "a refused claim or widening's own record of who waits for which files "
    "(services.waiting): globs and ids, no authored text",
    **dict.fromkeys(
        ("ask.requested", "ask.answered", "ask.withdrawn", "ask.expired", "ask.consumed"),
        "a question or approval put to a person and its outcome: the asker's text is "
        "compared by the asker, not here (services.ask)",
    ),
    "record.extended": "the duplicate check's own answer: text added onto a checked record",
    "link.recorded": "the duplicate check's own answer: two records judged related or the same",
    "repair.applied": "a versioned data repair's own record of the findings it settled "
    "(services.repairs): no authored text",
}
