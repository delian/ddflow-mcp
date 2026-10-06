"""The `[log]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class LogConfig:
    """Reading the append-only log — the cost every state-reading call pays."""

    reuse_parsed: bool = True
    max_cached_events: int = 100_000
    #: Commit ddflow's own event shards after complete and release (Bcd3512c891).
    commit_events: bool = True


_doc(
    "log",
    "reuse_parsed",
    "Re-use already-parsed events instead of re-parsing the whole log on every read. Sound because the log is APPEND-ONLY: a line that has been parsed can never change, so only the appended tail is new. Measured at 20k events: JSON parsing is 97ms of a 115ms read (84%), so this is where the time is. Set false to always re-read from scratch — slower, and the only reason to want it is a shard being rewritten in place under a running process, which `ddflow doctor` reports as a content-address mismatch anyway.",
)
_doc(
    "log",
    "commit_events",
    "After complete and release, commit the event shards (`.ddflow/events/*.jsonl`, nothing else) in the primary checkout, so a clone or a pull always gets the whole log. Hooks run as usual; a refusal never fails the operation and `ddflow doctor` lists shards left uncommitted. Nothing is pushed. false leaves committing the log to you.",
)
_doc(
    "log",
    "max_cached_events",
    "Memory ceiling for reuse_parsed, in events. Measured at ~736 bytes per parsed event, so the 100,000 default holds ~74 MB in a long-lived MCP server. Above the ceiling the cache is not used and reads cost what they always did — graceful, not a failure. A project big enough to hit this wants an on-disk state snapshot rather than a bigger process.",
)
