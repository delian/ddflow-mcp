"""The `[log]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("log")
@dataclass
class LogConfig:
    """Reading the append-only log — the cost every state-reading call pays."""

    reuse_parsed: bool = knob(
        True,
        doc="Re-use already-parsed events instead of re-parsing the whole log on every read. Sound because the log is APPEND-ONLY: a line that has been parsed can never change, so only the appended tail is new. Measured at 20k events: JSON parsing is 97ms of a 115ms read (84%), so this is where the time is. Set false to always re-read from scratch — slower, and the only reason to want it is a shard being rewritten in place under a running process, which `ddflow doctor` reports as a content-address mismatch anyway.",
    )
    max_cached_events: int = knob(
        100_000,
        doc="Memory ceiling for reuse_parsed, in events. Measured at ~736 bytes per parsed event, so the 100,000 default holds ~74 MB in a long-lived MCP server. Above the ceiling the cache is not used and reads cost what they always did — graceful, not a failure. A project big enough to hit this wants an on-disk state snapshot rather than a bigger process.",
    )
    #: Commit ddflow's own event shards after complete and release (Bcd3512c891).
    commit_events: bool = knob(
        True,
        doc="At the doors named by `commit_events_at` (merge, complete, release, stop), commit the event shards (`.ddflow/events/*.jsonl`, nothing else) in the primary checkout, so a clone or a pull always gets the whole log. Hooks run as usual; a refusal never fails the operation and `ddflow doctor` lists shards left uncommitted. Nothing is pushed. false leaves committing the log to you.",
    )
    commit_events_at: list[str] = knob(
        factory=lambda: ["merge", "complete", "release", "stop"],
        doc="The doors at which ddflow commits the event shards (needs commit_events): merge (after an item lands), complete, release, stop (a session ends). Drop one to leave that door's shards to the next.",
        check=lambda v: "; ".join(
            f"{d!r} is not a door (merge, complete, release, stop)"
            for d in v
            if d not in ("merge", "complete", "release", "stop")
        ),
    )
    commit_events_message: str = knob(
        "events: {reason}",
        doc="The commit message of an event-log commit; `{reason}` is the door and item, e.g. `complete T1`. Keep it short.",
    )
    pre_push_events: str = knob(
        "warn",
        choices=("warn", "block", "off"),
        strictest=("block", "a push never leaves event shards out"),
        doc="What scripts/ci/pre-push does when event shards in the primary checkout are not committed, so the push leaves them out: `warn` (default) names them, `block` refuses the push, `off` says nothing.",
    )
    #: The on-disk read snapshot (B166) as knobs (B-log-snapshot-knobs).
    snapshot: bool = knob(
        True,
        doc="Keep an on-disk read snapshot of the parsed log in `.ddflow/local/` so a cold one-shot command does not re-parse the whole log. It is a cache: every doubt ends in parsing the log, and the log stays the only source of truth. Needs `reuse_parsed`. `false` neither reads nor writes it; the environment variable `DDFLOW_SNAPSHOT=0` also turns it off for one process and wins over this setting.",
    )
    snapshot_min_events: int = knob(
        5000,
        doc="No snapshot is written below this many events: a cold parse is cheap there and a file is not free. Lower it to snapshot a small log (tests do), raise it to snapshot only a large one. 0 snapshots any log.",
        check=int_at_least(0),
    )
    snapshot_stale_days: int = knob(
        7,
        doc="Days a snapshot written by OTHER code (another ddflow version or parser) is kept before the next write collects it. One snapshot exists per version and parser, so worktrees on different versions do not overwrite each other's; this bounds how long an abandoned one stays.",
        check=int_at_least(1),
    )
    snapshot_touch_s: int = knob(
        86400,
        doc="A snapshot in use is marked as used (its modification time refreshed) at most this often, in seconds, so a long-lived reader's snapshot is not collected as abandoned by a sibling checkout on other code. 0 refreshes it on every read.",
        check=int_at_least(0),
    )
