"""The committed log: which fields of which event kind are free text, and the per-event
redaction that applies them (D-unify 6).

`scripts/event_kind.py` reads and edits `LOG_TEXT_FIELDS` and `LOG_VERBATIM_FIELDS` from
THIS source, so their shape is part of the scaffold's contract: each stays a literal
mapping the script can `ast.literal_eval`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .engine import _Pass
from .pattern import _HOST, _IPV4, _IPV6, _is_private, _loop

if TYPE_CHECKING:
    from .profiles import Redactor

#: Event kind -> the `data` fields that hold free text. `EventLog._write` runs these through
#: the `log` profile, so a secret, a LAN address, the machine's hostname or a home path in a
#: gate's output tail, a bug summary or a lesson is never committed (bug B5deba76d04).
#: A string field is redacted whole; a dict or list field is redacted leaf by leaf, whatever
#: its keys. These are the fields that are text even where their name is a known lookup name.
LOG_TEXT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "token.revoked": ("reason",),
    "probe.recorded": ("why",),
    "suggestion.expired": ("reason",),
    "suggestion.superseded": ("reason",),
    "suggestion.withdrawn": ("reason",),
    "suggestion.countered": (
        "reason",
        "counter",
    ),
    "suggestion.rejected": ("reason",),
    "suggestion.applied": ("reason",),
    "suggestion.accepted": ("reason",),
    "suggestion.seen": ("reason",),
    "suggestion.proposed": (
        "reason",
        "probe",
    ),
    "phase.completed": ("evidence",),
    "memory.redacted": ("reason",),
    "approval.granted": ("note",),
    "ask.answered": ("answer",),
    "ask.requested": ("text",),
    "bug.fixed": ("lesson", "changelog", "regression_verify"),
    "bug.found": ("summary", "title"),
    "bug.invalid": ("evidence", "reason"),
    "bug.reopened": ("reason",),
    "cadence.ran": ("evidence", "result"),
    "ci.result": ("checks",),
    "decision.recorded": (
        "alternatives",
        "consequences",
        "context",
        "decision",
        "title",
        "sources",
    ),
    "decision.superseded": ("reason",),
    "def.merged": ("reason",),
    "def.recorded": ("fields", "reason"),
    "def.retired": ("reason",),
    "def.superseded": ("reason",),
    "def.updated": ("fields", "reason"),
    "flow.chosen": ("reason", "value"),
    "gate.failed": ("evidence", "reason"),
    "gate.partial": ("evidence", "reason"),
    "gate.passed": ("evidence", "reason"),
    "gate.skipped": ("evidence", "reason"),
    "gate.unavailable": ("evidence", "reason"),
    "item.abandoned": ("reason",),
    "item.blocked": ("reason",),
    "item.completed": ("changelog", "evidence", "not_applicable", "chore"),
    "item.reopened": ("reason", "claims"),
    "item.unblocked": ("note",),
    "job.ended": ("note",),
    "lease.acquired": ("note",),
    "job.started": ("command",),
    "lease.expired": ("reason",),
    "lease.released": ("note", "reason"),
    "lesson.recorded": ("how", "pattern", "rule", "summary", "title", "why", "sites"),
    "memory.forgotten": ("reason",),
    "memory.recorded": ("text", "source"),
    "phase.added": ("body", "title", "line"),
    "phase.updated": ("body", "title"),
    "pr.synced": ("feedback",),
    "record.extended": ("text",),
    "research.recorded": (
        "claim",
        "falsifier",
        "mechanism",
        "probe",
        "probe_output",
        "question",
        "sources",
    ),
    "review.triaged": ("title", "probe"),
    "schedule.defined": ("title",),
    "schedule.removed": ("reason",),
    "session.ended": ("summary",),
    "session.note": ("text", "source"),
    "session.prompt": ("text",),
    "session.started": ("cwd",),
    "skew.overridden": ("reason",),
    "task.added": ("body", "title", "line"),
    "task.removed": ("reason",),
    "task.updated": ("body", "title"),
    "trigger.evaluated": ("errors",),
    "trigger.suppressed": ("detail", "reason"),
}

#: Field NAMES that are ids, digests, shas, paths or numbers something looks up by, in every
#: kind: written as given. The default is the other way round -- a field not listed here (or in
#: `LOG_VERBATIM_FIELDS`) is redacted, so a field the vocabulary does not know of (a `--note` on a
#: claim, a `--reason` on a link, a review's feedback) cannot be committed as typed.
LOG_VERBATIM_NAMES = frozenset(
    {
        "base", "branch", "digest", "duplicate_of", "extends", "fix_task", "fixes", "globs",
        "hash", "head", "id", "ids", "needs", "number", "parent", "path", "paths", "related",
        "resources", "sha", "subject", "supersedes", "tags", "tree", "url", "v", "worktree",
    }
)  # fmt: skip

#: (kind, field) pairs that are looked up, compared or opened, though their name is not in
#: `LOG_VERBATIM_NAMES`, each with why.
#: Field names that are free text in every kind (the dedupe verdict quotes candidates).
LOG_TEXT_NAMES = frozenset({"dedupe"})

LOG_VERBATIM_FIELDS: Mapping[tuple[str, str], str] = {
    ("token.revoked", "name"): "the client name, its identity, compared",
    ("token.revoked", "by"): "an actor id, the audit trail's who",
    ("token.issued", "name"): "the client name, its identity, compared",
    ("token.issued", "role"): "a role word",
    ("token.issued", "expires_at"): "a time that is compared",
    ("token.issued", "by"): "an actor id, the audit trail's who",
    ("token.issued", "rotated_from"): "a token id that is compared",
    ("probe.recorded", "against"): "the ref the item was probed against",
    ("probe.recorded", "result"): "clean, conflict or unavailable",
    ("probe.recorded", "method"): "merge-tree, scratch-merge or none",
    ("suggestion.expired", "role"): "a role word: ddflow or operator",
    ("suggestion.superseded", "role"): "a role word: ddflow or operator",
    ("suggestion.superseded", "by_id"): "the id of the replacing suggestion",
    ("suggestion.withdrawn", "role"): "a role word: ddflow or operator",
    ("suggestion.countered", "role"): "a role word: ddflow or operator",
    ("suggestion.rejected", "role"): "a role word: ddflow or operator",
    ("suggestion.applied", "role"): "a role word: ddflow or operator",
    ("suggestion.accepted", "role"): "a role word: ddflow or operator",
    ("suggestion.seen", "role"): "a role word: ddflow or operator",
    ("suggestion.proposed", "from_item"): "an item id",
    ("suggestion.proposed", "from_agent"): "an agent id, the audit trail's who",
    ("suggestion.proposed", "to_item"): "an item id",
    ("suggestion.proposed", "to_agent"): "an agent id, the audit trail's who",
    ("suggestion.proposed", "ref"): "a hidden git ref name",
    ("suggestion.proposed", "patch_id"): "a git patch-id hash that is compared",
    ("suggestion.proposed", "blob"): "a blob address that is compared",
    (
        "suggestion.proposed",
        "files",
    ): "the paths the patch touches, compared with the owner's leases",
    ("suggestion.proposed", "added"): "a number",
    ("suggestion.proposed", "removed"): "a number",
    ("suggestion.proposed", "kind"): "a kind word",
    ("phase.completed", "kind"): "a kind word",
    ("lane.released_for_gate", "holder"): "an agent id compared with the lease's holder",
    ("lane.released_for_gate", "author"): "a family or model id compared at completion",
    ("lane.released_for_gate", "judgement"): "gate ids",
    ("lane.released_for_gate", "gates"): "gate ids",
    (
        "memory.redacted",
        "event",
    ): "the id of the superseded memory.recorded event that was redacted",
    ("lease.waiting", "holder"): "an agent id compared with a lease's holder",
    ("lease.waiting", "lease_event"): "an event id compared with the waiter's lease",
    ("lease.waiting", "on"): "item ids, holders and glob pairs of the leases waited for",
    ("approval.granted", "token_hash"): "a hash that is compared",
    ("approval.used", "token_hash"): "a hash that is compared",
    ("ask.requested", "code_hash"): "a hash that is compared",
    ("ask.requested", "expires_at"): "a time that is compared",
    ("ask.requested", "type"): "a kind word",
    ("ask.requested", "options"): "the choices an answer is compared with",
    ("ask.requested", "verb"): "a gated-command verb word",
    ("ask.requested", "tier"): "a tier word",
    ("ask.requested", "min_level"): "a number",
    ("ask.requested", "uses"): "a number",
    ("ask.requested", "use_ttl_s"): "a number",
    ("ask.requested", "canon"): "the canonical command record, compared by its digest",
    ("ask.consumed", "by"): "an agent id, the audit trail's who",
    ("ask.consumed", "grade"): "an approval grade word such as L2-typed",
    ("ask.consumed", "verb"): "a gated-command verb word",
    ("ask.consumed", "effect"): "an event id compared with the effect it stamped",
    ("ask.answered", "outcome"): "the word or choice an answer is compared with",
    ("ask.answered", "role"): "a role name",
    ("ask.answered", "via"): "a channel word",
    ("ask.answered", "source"): "an actor source word",
    ("ask.answered", "level"): "a number",
    ("ask.answered", "policy"): "a flag: a standing rule answered, not a person",
    ("ask.answered", "proof"): "a channel's own evidence id, compared with the record it names",
    (
        "ask.answered",
        "code_presented",
    ): "the raw, already-spent one-time code (re-hashed by verdict to compare)",
    ("ask.answered", "by"): "an actor id, the audit trail's who",
    ("ask.withdrawn", "by"): "an actor id, the audit trail's who",
    ("ask.expired", "by"): "an actor id, the audit trail's who",
    ("worktree.merged", "branch_head"): "a sha",
    ("worktree.merged", "outside_globs"): "globs",
    ("schedule.defined", "scope_globs"): "globs",
    ("pr.synced", "head_sha"): "a sha",
    ("pr.synced", "merge_sha"): "a sha",
    ("approval.granted", "user"): "a user id",
    ("backmerge.recorded", "into"): "a branch name",
    ("bug.found", "key"): "a dedupe key",
    ("bug.found", "repro"): "the link to a stored bundle: its blob reference, size and mode",
    ("bug.found", "scope"): "a scope word",
    ("bug.tagged", "add"): "the tags added: label words",
    ("bug.tagged", "remove"): "the tags removed: label words",
    ("bug.found", "severity"): "a severity word",
    ("ci.result", "status"): "a status word",
    ("ddflow.seen", "version"): "a version",
    ("ddflow.seen", "format_level"): "a format level",
    ("ddflow.capabilities", "capability"): "a capability name",
    ("ddflow.capabilities", "kinds"): "kind words",
    ("ddflow.capabilities", "version"): "a version",
    ("decision.recorded", "item"): "an item id",
    ("decision.recorded", "status"): "a status word",
    ("def.merged", "source"): "an origin id or path",
    ("def.recorded", "source"): "an origin id or path",
    ("def.retired", "source"): "an origin id or path",
    ("def.superseded", "source"): "an origin id or path",
    ("def.updated", "source"): "an origin id or path",
    ("export.disabled", "document"): "a document name",
    ("export.enabled", "document"): "a document name",
    ("export.enabled", "mode"): "a mode word",
    ("flow.chosen", "knob"): "a config key",
    ("flow.chosen", "user"): "a user id",
    ("gate.failed", "gate"): "a gate name",
    ("gate.failed", "kind"): "a kind word",
    ("gate.out_of_order", "gate"): "a gate name",
    ("gate.out_of_order", "policy"): "a policy word",
    ("gate.partial", "gate"): "a gate name",
    ("gate.passed", "gate"): "a gate name",
    ("gate.passed", "kind"): "a kind word",
    ("gate.skipped", "gate"): "a gate name",
    ("gate.unavailable", "gate"): "a gate name",
    ("job.started", "host"): "a machine id compared for liveness",
    ("job.started", "item"): "an item id",
    ("job.started", "log"): "a path that is opened",
    ("job.started", "proc_start"): "a process start stamp",
    ("lease.acquired", "holder"): "an agent id",
    ("lease.acquired", "kind"): "a kind word",
    ("lease.expired", "holder"): "an agent id",
    ("lease.expired", "broken_by"): "the agent id that broke the lease",
    ("lease.expired", "broken_for"): "the item id the break was made for",
    ("lease.expired", "files"): "the glob pair the break was about",
    ("lease.released", "by"): "an agent id",
    ("lease.released", "event"): "an event id",
    ("lease.released", "holder"): "an agent id",
    ("link.recorded", "relation"): "a relation word",
    ("link.recorded", "target"): "an id",
    ("phase.added", "source"): "an origin id or path",
    ("pr.synced", "kind"): "a kind word",
    ("pr.synced", "queue_state"): "a state word",
    ("pr.synced", "state"): "a state word",
    ("research.recorded", "verdict"): "a verdict word",
    ("review.triaged", "severity"): "a severity word",
    ("review.triaged", "verdict"): "a verdict word",
    ("reviewer.configured", "user"): "a user id",
    ("session.note", "item"): "an item id",
    ("session.started", "tool"): "a harness name",
    ("skew.overridden", "session"): "a session id",
    ("task.added", "port_from"): "an id",
    ("task.added", "port_of"): "an id",
    ("task.added", "port_strategy"): "a strategy word",
    ("task.added", "source"): "an origin id or path",
    ("trigger.fired", "items"): "item ids",
    ("trigger.fired", "job"): "a job id",
    ("trigger.fired", "key"): "a dedupe key",
    ("trigger.suppressed", "key"): "a dedupe key",
    ("approval.granted", "host"): "a machine id compared by liveness",
    ("backmerge.recorded", "forge"): "a forge name",
    ("bug.found", "item"): "an item id",
    ("ci.result", "stage"): "a stage word",
    ("ddflow.seen", "install"): "an install kind",
    ("decision.recorded", "decided_by"): "an agent id",
    ("decision.superseded", "by"): "an agent id",
    ("def.merged", "kind"): "a kind word",
    ("def.recorded", "kind"): "a kind word",
    ("def.retired", "kind"): "a kind word",
    ("def.superseded", "kind"): "a kind word",
    ("def.updated", "kind"): "a kind word",
    ("deploy.recorded", "env"): "an environment name",
    ("export.disabled", "by"): "an agent id",
    ("export.enabled", "by"): "an agent id",
    ("flow.chosen", "by"): "an agent id",
    ("gate.failed", "by"): "an agent id",
    ("gate.out_of_order", "ahead"): "gate names",
    ("gate.partial", "by"): "an agent id",
    ("gate.passed", "by"): "an agent id",
    ("gate.skipped", "by"): "an agent id",
    ("gate.started", "gate"): "a gate name",
    ("gate.unavailable", "by"): "an agent id",
    ("item.abandoned", "kind"): "a kind word",
    ("item.blocked", "kind"): "a kind word",
    ("item.completed", "kind"): "a kind word",
    ("item.resolved", "kind"): "a kind word",
    ("item.unblocked", "kind"): "a kind word",
    ("job.started", "cwd"): "a directory that is opened",
    ("lease.acquired", "agent"): "an agent id",
    ("lease.expired", "event"): "an event id",
    ("lease.released", "agent"): "an agent id",
    ("lease.renewed", "holder"): "an agent id",
    ("lesson.recorded", "seen_in"): "item ids",
    ("link.recorded", "by"): "an agent id",
    ("memory.recorded", "origin_at"): "a timestamp",
    ("phase.added", "kind"): "a kind word",
    ("pr.synced", "forge"): "a forge name",
    ("record.extended", "relation"): "a relation word",
    ("research.recorded", "item"): "an item id",
    ("review.triaged", "gate"): "a gate name",
    ("reviewer.configured", "kind"): "a kind word",
    ("session.note", "at"): "a timestamp",
    ("session.prompt", "item"): "an item id",
    ("session.started", "model"): "a model id",
    ("skew.overridden", "log_version"): "a version",
    ("skew.overridden", "log_format"): "a format level",
    ("task.added", "kind"): "a kind word",
    ("trigger.evaluated", "triggers"): "trigger ids",
    ("trigger.fired", "events"): "event ids",
    ("trigger.suppressed", "events"): "event ids",
    ("bug.fixed", "regression_verified"): "an outcome word",
    ("bug.reopened", "was"): "a state word",
    ("item.unblocked", "was"): "a state word",
    ("gate.partial", "outcome"): "an outcome word",
    ("pr.synced", "checks"): "a status word",
    ("record.extended", "who"): "an agent id",
    ("research.recorded", "budget"): "a tier word",
    ("review.triaged", "location"): "file:line, a lookup",
    ("schedule.defined", "cadence"): "numbers and cron words",
    ("skew.overridden", "running"): "a version",
    ("worktree.merged", "landed_after"): "a sha",
    ("bug.fixed", "regression_test"): "a test id",
    ("def.superseded", "successor"): "an id",
    ("def.merged", "successor"): "an id",
    ("item.completed", "ledger"): "files, tests and counts",
    ("item.resolved", "claim"): "re-applied by the fold: a lease with its worktree and event ids",
    (
        "item.resolved",
        "definition",
    ): "re-applied by the fold; its text was redacted when first written",
    ("schedule.defined", "concurrency_group"): "a group id",
    ("pr.synced", "queue"): "a queue state word",
    ("worktree.merged", "landed_before"): "a sha",
    ("trigger.evaluated", "now"): "a timestamp",
    ("bug.fixed", "regression_tests"): "test ids",
    ("item.completed", "overridden"): "gate names",
    ("item.resolved", "keep"): "an id",
    ("pr.synced", "review"): "a review state word",
    ("def.merged", "provenance"): "ids of origin",
    ("def.recorded", "provenance"): "ids of origin",
    ("def.retired", "provenance"): "ids of origin",
    ("def.retired", "split_into"): "ids of the parts a split made",
    ("def.superseded", "provenance"): "ids of origin",
    ("def.updated", "provenance"): "ids of origin",
}

#: `(kind, field)` pairs that are compared byte-for-byte and are never a URL or a path, so
#: the network pass must skip them: a gated-command verb word is compared with the canonical
#: verb (`approvals.consume`), and one whose site ends in `.local` must not be rewritten into
#: `[REDACTED:host]` (bug B31f83a9d7b). Other verbatim fields that CAN carry a private host
#: (url, path, worktree, branch, tags, globs) keep the network pass (bug Ba2df74d558).
LOG_NO_NETWORK_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ask.requested", "verb"),
        ("ask.consumed", "verb"),
    }
)


def redact_network(text: str) -> str:
    """`text` with only its private addresses and private host names marked: the pass a
    lookup field gets, since an id or a sha has neither and a URL or path may (Ba2df74d558)."""
    p = _Pass(text)
    for pat, kind, ip_type in (
        (_IPV4, "ipv4", ipaddress.IPv4Address),
        (_IPV6, "ipv6", ipaddress.IPv6Address),
    ):
        # loopback is the default of a local endpoint: not a leak, and the repo guard allows it
        p.sub(pat, kind, keep=lambda m, t=ip_type: not _is_private(m.group(1), t) or _loop(m, t))
    p.sub(_HOST, "host")
    return p.text


def _map_leaves(value: object, fn) -> object:
    """``value`` with ``fn`` applied to every string leaf, whatever its key."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, Mapping):
        return {k: _map_leaves(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_map_leaves(v, fn) for v in value]
    return value


def network_leaves(value: object) -> object:
    """``value`` with `redact_network` applied to every string leaf."""
    return _map_leaves(value, redact_network)


def redact_leaves(value: object, redactor: Redactor) -> object:
    """``value`` with every string leaf redacted, whatever its key: the keys of a text field
    come from data (gate names, `def` fields), and nothing looks a leaf up in the log."""
    return _map_leaves(value, lambda s: redactor.text(s).text)


def redact_event_data(
    kind: str, data: dict, redactor: Redactor, *, lookup_network: bool = True
) -> dict:
    """``data`` of an event of ``kind`` with its text redacted (a new dict).

    The declared text fields (`LOG_TEXT_FIELDS`) are redacted; so is every field that is not
    a known lookup (`LOG_VERBATIM_NAMES`, `LOG_VERBATIM_FIELDS`): unknown is not text-free.
    A lookup keeps its bytes except a private address or host in it, `lookup_network`
    (`[session].redact_lookup_network`; bug Ba2df74d558); a field in `LOG_NO_NETWORK_FIELDS`
    is compared byte-for-byte and keeps its bytes exactly."""
    text = LOG_TEXT_FIELDS.get(kind, ())
    out = {}
    for k, v in data.items():
        if k not in text and (k in LOG_VERBATIM_NAMES or (kind, k) in LOG_VERBATIM_FIELDS):
            if (kind, k) in LOG_NO_NETWORK_FIELDS:
                out[k] = v
            else:
                out[k] = network_leaves(v) if lookup_network else v
        else:
            out[k] = redact_leaves(v, redactor)
    return out
