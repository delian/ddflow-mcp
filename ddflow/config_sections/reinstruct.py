"""The `[reinstruct]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class ReinstructConfig:
    """Re-stating the rules after a compaction — the one channel that survives it.

    `initialize` delivers the instruction block ONCE. After a context compaction the model
    may retain none of it, and MCP has no server->client context-injection primitive: the
    three that exist are `roots/list`, `sampling/createMessage` and `elicitation/create`,
    and none of them injects anything. A footer on tool results is the only place the
    server is guaranteed to be heard again, because an agent driving ddflow calls tools
    continuously.

    ON by default, and cadenced rather than cheap-by-accident. An opt-in feature nobody
    enables does not solve the problem it was filed for; a footer on all 63 tools would be
    trained out inside a session and would cost tokens on every call. So it speaks at most
    once every `every_calls` calls AND `every_seconds` seconds, and only when there is
    something specific to say — see `services/obligations.py`.
    """

    enabled: bool = True
    every_calls: int = 12
    every_seconds: int = 240
    max_items: int = 3


_doc(
    "reinstruct",
    "enabled",
    "Whether tool results may carry a short footer naming what this project has left undone. The instruction block is delivered once at connect; after a context compaction nothing else re-states it, and MCP has no primitive for injecting context. Set false to silence it entirely.",
)
_doc(
    "reinstruct",
    "every_calls",
    "Minimum tool calls between two footers. A footer on every call is a banner readers learn to skip.",
)
_doc(
    "reinstruct",
    "every_seconds",
    "Minimum seconds between two footers. BOTH this and every_calls must be satisfied, so a burst of calls does not produce a burst of footers.",
)
_doc(
    "reinstruct",
    "max_items",
    "How many outstanding obligations a footer names. Longer than this and it is scrolled past rather than read.",
)
