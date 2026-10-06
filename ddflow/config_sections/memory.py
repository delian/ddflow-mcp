"""The `[memory]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("memory")
@dataclass
class MemoryConfig:
    """Operational memory: short facts about this machine and repository, `[memory]`."""

    max_chars: int = knob(
        280,
        doc="Longest memory `ddflow memory add` accepts. A memory is ONE operational fact ('this box has 8 H200s'); something longer is a lesson or a journal entry, and a store of paragraphs is one nobody reads at session start. 280 is the OptMem record width the source projects used.",
    )
    brief_items: int = knob(
        12,
        doc="How many live memories `ddflow brief` shows, newest first. They are what an agent must know before touching anything on this machine, so they sit near the top of the brief; the rest are one `ddflow memory list` or `recall` away. 0 leaves them out of the brief.",
    )
