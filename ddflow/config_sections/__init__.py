"""The `[section]` dataclasses of `ddflow.config`, one module per section.

`ddflow.config` re-exports every name defined here and assembles `Config` from the
section classes; import from `ddflow.config`, not from here. Each section module holds its
dataclass, the values it accepts and its knob docs (`_docs.KNOB_DOCS`). `config --explain`
lists the knobs in `Config`'s field order, not in the order these modules are imported.
"""

from __future__ import annotations

import importlib
import pkgutil

#: Every section module: each public module of this package, found by convention. A new
#: `[section]` is a new file here and no list names it. Nothing depends on the order:
#: `config --explain` follows `Config`'s field order.
SECTION_NAMES = tuple(
    sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))
)

SECTION_MODULES = tuple(importlib.import_module(f"{__name__}.{n}") for n in SECTION_NAMES)
