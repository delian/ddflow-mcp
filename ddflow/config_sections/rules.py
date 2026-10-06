"""The `[rules]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class RulesConfig:
    """Project rules schema and storage configuration.

    Rules are project-specific patterns, guidelines, or constraints encoded in the
    queue. Configurable limits and allowed values for rules.
    """

    max_rules: int = 200
    max_size_bytes: int = 50000
    tags_allowed: list[str] = field(default_factory=list)  # empty = any
    scopes_allowed: list[str] = field(default_factory=lambda: ["project", "phase", "task"])


_doc(
    "rules",
    "max_rules",
    "Maximum number of rules a project may define. Prevents sprawl; enforced on rule creation.",
)
_doc(
    "rules",
    "max_size_bytes",
    "Maximum size in bytes for a single rule's content. Prevents rules from becoming unwieldy.",
)
_doc(
    "rules",
    "tags_allowed",
    "Whitelist of allowed tag values for rules. Empty (default) means any tag is allowed. Set to enforce a controlled vocabulary.",
)
_doc(
    "rules",
    "scopes_allowed",
    "Which scopes a rule may declare. Defaults to project, phase, task. Can be restricted to a subset.",
)
