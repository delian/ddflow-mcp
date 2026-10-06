"""The `[lease]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class LeaseConfig:
    """Crash-recoverable ownership of a work item."""

    ttl_s: int = 1800
    heartbeat_s: int = 300
    grace_s: int = 120
    acquire_timeout_s: int = 30
    #: How long a waiter keeps its place in line once it could claim (see the knob doc).
    waiter_reservation_s: int = 300
    reclaim_policy: str = "report"  # report | auto
    #: Paths many items may hold at once (D-shared-globs). See the knob docs below.
    shared_globs: list[str] = field(default_factory=list)
    append_only_globs: list[str] = field(default_factory=list)


_doc(
    "lease",
    "ttl_s",
    "Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate; shorter = faster recovery after a crash.",
)
_doc(
    "lease",
    "heartbeat_s",
    "How often a live agent renews its lease. Must be comfortably below ttl_s (a 6x margin is the default) or a slow turn looks like a crash.",
)
_doc(
    "lease",
    "grace_s",
    "Extra slack added to ttl_s before an expired lease is reported as reclaimable, absorbing clock skew between machines sharing an NFS checkout.",
)
_doc(
    "lease",
    "acquire_timeout_s",
    "How long to block on the flock arbitrating lease acquisition before giving up. On NFS a contended acquire measured ~135 ms, so 30 s is ~200x headroom.",
)
_doc(
    "lease",
    "shared_globs",
    'Files EVERY item edits that ddflow must not merge for you -- generated files (a regenerated config, a lockfile): ["configs/default.toml"]. A path inside one of these is exempt from lease-overlap checks (claim, update, next, wait) and counts as covered for any agent holding a live lease at commit time. ddflow does NOT write a merge driver for them: union-merging a generated file interleaves it. Regenerate it after merging, or declare a driver yourself in .gitattributes; doctor notes a shared glob that has none. For append-only files (a changelog) use append_only_globs instead.',
)
_doc(
    "lease",
    "append_only_globs",
    'Files every item APPENDS to -- a changelog, a research log: ["docs/CHANGELOG.md"]. Shared like shared_globs (no lease-overlap check; covered for any live lease holder), and ddflow WRITES "<glob> merge=union" to .gitattributes for each one, so two items\' added lines both survive the merge. Written when this is set through `ddflow config --set/--append-toml` (or ddflow_configure), and re-synced by `ddflow init`/`adopt` after a hand edit; .gitattributes is tracked, so commit it with the config. Only globs in the COMMITTED config get a line: one set with --local is this machine\'s and writes no rule for every clone. doctor reports a missing line.',
)
_doc(
    "lease",
    "reclaim_policy",
    "'report' (default) never steals an expired lease — it names the worktree so a human can rescue in-flight work. 'auto' reclaims it. 'report' exists because a killed agent leaves FINISHED, uncommitted work behind more often than it leaves garbage.",
)
_doc(
    "lease",
    "waiter_reservation_s",
    "Claims on a contended file are served first come, first served. A live `ddflow wait` (or a claim refused for an overlap and retried) is a place in line; while a waiter is next and its item could be claimed now, a younger or unqueued claim of an overlapping file is refused as 'reserved for <agent>'. The place lapses this many seconds after the waiter could claim (it woke and did not come back), and at once when its process died or its wait deadline passed, so a dead or slow waiter never blocks anyone for long. 0 turns the queue off: whoever claims first wins.",
)
