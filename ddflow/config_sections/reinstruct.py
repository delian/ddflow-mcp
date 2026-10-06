"""The `[reinstruct]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass


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
