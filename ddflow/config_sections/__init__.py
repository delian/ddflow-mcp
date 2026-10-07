"""The `[section]` dataclasses of `ddflow.config`, one module per section.

`ddflow.config` re-exports every name defined here and assembles `Config` from the
section classes; import from `ddflow.config`, not from here. Each section module holds its
dataclass, the values it accepts and its knob docs (`_docs.KNOB_DOCS`). `config --explain`
lists the knobs in `Config`'s field order, not in the order these modules are imported.
"""

from __future__ import annotations

import importlib

#: Every section module, in the order their dataclasses sat in the single `config.py` before
#: the split. Nothing depends on it: `config --explain` follows `Config`'s field order.
SECTION_NAMES = (
    "lease",
    "worktree",
    "flow",
    "gates",
    "lessons",
    "session",
    "schedule",
    "bugs",
    "memory",
    "dedupe",
    "imports",
    "companions",
    "reinstruct",
    "cadence",
    "rules",
    "ci",
    "prompts",
    "log",
    "upgrade",
    "release",
    "mcp",
    "export",
    "loops",
    "review",
    "enforce",
    "agent",
    "ids",
    "triggers",
)

SECTION_MODULES = tuple(importlib.import_module(f"{__name__}.{n}") for n in SECTION_NAMES)
