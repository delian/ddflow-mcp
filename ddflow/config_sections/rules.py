"""The `[rules]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("rules")
@dataclass
class RulesConfig:
    """Project rules schema and storage configuration.

    Rules are project-specific patterns, guidelines, or constraints encoded in the
    queue. Configurable limits and allowed values for rules.
    """

    max_rules: int = knob(
        200,
        doc="Maximum number of rules a project may define. Prevents sprawl; enforced on rule creation.",
    )
    max_size_bytes: int = knob(
        50000,
        doc="Maximum size in bytes for a single rule's content. Prevents rules from becoming unwieldy.",
    )
    tags_allowed: list[str] = knob(
        factory=list,
        doc="Whitelist of allowed tag values for rules. Empty (default) means any tag is allowed. Set to enforce a controlled vocabulary.",
    )
    scopes_allowed: list[str] = knob(
        factory=lambda: ["project", "phase", "task"],
        doc="Which scopes a rule may declare. Defaults to project, phase, task. Can be restricted to a subset.",
    )
