"""The `[lease]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("lease")
@dataclass
class LeaseConfig:
    """Crash-recoverable ownership of a work item."""

    ttl_s: int = knob(
        1800,
        doc="Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate; shorter = faster recovery after a crash.",
    )
    heartbeat_s: int = knob(
        300,
        doc="How often a live agent renews its lease. Must be comfortably below ttl_s (a 6x margin is the default) or a slow turn looks like a crash.",
    )
    renew_log_min_s: int = knob(
        120,
        doc="A heartbeat is written to the log only when the lease's last written renewal is at least this many seconds old; closer ones are acknowledged and not recorded, so a busy agent's hooks do not fill the log (lease renewals were 43% of a real log's events). A claim, a re-claim and any renewal that changes the worktree, branch, globs or resources are always written. Cost: another clone sees this lease's expiry up to this much earlier than the holder's last heartbeat implies, so the window in force is never wider than heartbeat_s or grace_s (the slack the lease already allows): raising this alone changes nothing. 0 writes every heartbeat.",
        check=int_at_least(0),
    )
    grace_s: int = knob(
        120,
        doc="Extra slack added to ttl_s before an expired lease is reported as reclaimable, absorbing clock skew between machines sharing an NFS checkout.",
    )
    acquire_timeout_s: int = knob(
        30,
        doc="How long to block on the flock arbitrating lease acquisition before giving up. On NFS a contended acquire measured ~135 ms, so 30 s is ~200x headroom.",
    )
    #: How long a waiter keeps its place in line once it could claim (see the knob doc).
    waiter_reservation_s: int = knob(
        300,
        doc="Claims on a contended file are served first come, first served. A live `ddflow wait` (or a claim refused for an overlap and retried) is a place in line; while a waiter is next and its item could be claimed now, a younger or unqueued claim of an overlapping file is refused as 'reserved for <agent>'. The place lapses this many seconds after the waiter could claim (it woke and did not come back), and at once when its process died or its wait deadline passed, so a dead or slow waiter never blocks anyone for long. 0 turns the queue off: whoever claims first wins.",
    )
    wait_max_s: int = knob(
        600,
        doc="A claim or `update --globs` refused for files another lease holds returns at once and leaves a waiting record (who, which files, since when) instead of making the agent poll while it holds its old files. A waiter that has waited longer than this many seconds while OTHER agents wait for files in its own lease is told which of its own globs to release (`update --globs` without them) or to report a cycle; nothing is taken from it. 0 never tells it.",
    )
    wait_queue: bool = knob(
        False,
        doc="Keep every refused request in a first-in-first-out line per file: a later request for files an earlier waiter asked for is refused as 'queued behind <item>' until that waiter has them or its wait ends. `claim --queue` and `update --queue` ask for it per request. Off: a refusal is only recorded, and whoever asks first when the files free up wins.",
    )
    #: report | auto
    reclaim_policy: str = knob(
        "report",
        doc="'report' (default) never steals an expired lease — it names the worktree so a human can rescue in-flight work. 'auto' reclaims it. 'report' exists because a killed agent leaves FINISHED, uncommitted work behind more often than it leaves garbage.",
        choices=("report", "auto"),
        strictest=("report", "never steals a lease, so a crashed agent's work survives"),
    )
    #: Paths many items may hold at once (D-shared-globs).
    shared_globs: list[str] = knob(
        factory=list,
        doc='Files EVERY item edits that ddflow must not merge for you -- generated files (a regenerated config, a lockfile): ["configs/default.toml"]. A path inside one of these is exempt from lease-overlap checks (claim, update, next, wait) and counts as covered for any agent holding a live lease at commit time. ddflow does NOT write a merge driver for them: union-merging a generated file interleaves it. Regenerate it after merging, or declare a driver yourself in .gitattributes; doctor notes a shared glob that has none. For append-only files (a changelog) use append_only_globs instead.',
    )
    append_only_globs: list[str] = knob(
        factory=list,
        doc='Files every item APPENDS to -- a changelog, a research log: ["docs/CHANGELOG.md"]. Shared like shared_globs (no lease-overlap check; covered for any live lease holder), and ddflow WRITES "<glob> merge=union" to .gitattributes for each one, so two items\' added lines both survive the merge. Written when this is set through `ddflow config --set/--append-toml` (or ddflow_configure), and re-synced by `ddflow init`/`adopt` after a hand edit; .gitattributes is tracked, so commit it with the config. Only globs in the COMMITTED config get a line: one set with --local is this machine\'s and writes no rule for every clone. doctor reports a missing line.',
    )
