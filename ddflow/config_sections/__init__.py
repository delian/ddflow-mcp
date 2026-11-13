"""The `[section]` dataclasses of `ddflow.config`, one module per section.

`ddflow.config` re-exports every name defined here and assembles `Config` from the
section classes; import from `ddflow.config`, not from here. Each section module holds its
dataclass, the values it accepts and its knob docs (`_docs.KNOB_DOCS`). `config --explain`
lists the knobs in `Config`'s field order, not in the order these modules are imported.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

#: The sorted package scan, shared with the adapter ports (one scanner, no drift): see
#: `ddflow.adapters.ports.module_names`.
from ..adapters.ports import module_names as _module_names


def module_names(package: ModuleType, skip: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """The public submodule names of ``package``, sorted (one deterministic order, no hash
    order), private ``_``-prefixed modules left out. This is `ports.module_names` with the
    config-sections convention layered on: a section is a public file here, so a section added
    to a package on ``package.__path__`` needs no edit to any shared file."""
    return tuple(n for n in _module_names(package, skip) if not n.startswith("_"))


#: Every section module: each public module of this package, found by convention. A new
#: `[section]` is a new file here: its `@declare("<name>")` maps the section name to its
#: dataclass in `_docs.SECTION_CLASSES`, from which `Config` assembles its fields and
#: `ddflow.config` re-exports the class (r-extension-contract). `config --explain` follows
#: `Config`'s field order, which is alphabetical by section name.
SECTION_NAMES = module_names(sys.modules[__name__])

SECTION_MODULES = tuple(importlib.import_module(f"{__name__}.{n}") for n in SECTION_NAMES)
