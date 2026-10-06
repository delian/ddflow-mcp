"""The `[companions]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class CompanionsConfig:
    """Detecting the MCP servers that serve this project's gates: `[companions]`."""

    probe_cache_ttl_s: int = 300


_doc(
    "companions",
    "probe_cache_ttl_s",
    "How long a companion detection result stays good, in seconds. Each probe shells out and an npx-based one takes seconds on a cold cache, so a session-start hook that listed companions paid that every time. A NEGATIVE result is cached like a positive one; an INCONCLUSIVE one never is, because 'could not tell' is a transient fact and caching it would make a blip stick for the whole window. 0 disables the cache.",
)
