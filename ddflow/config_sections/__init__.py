"""The `[section]` dataclasses of `ddflow.config`, one module per section.

`ddflow.config` re-exports every name defined here and assembles `Config` from the
section classes; import from `ddflow.config`, not from here. Each section module holds its
dataclass, the values it accepts and its knob docs (`_docs.KNOB_DOCS`). `config --explain`
lists the knobs in `Config`'s field order, not in the order these modules are imported.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType


def module_names(package: ModuleType, skip: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """The public submodule names of ``package``, sorted: the one deterministic order (no hash
    order), private modules left out. New sections are discovered by this scan, so a section
    added to a package on ``package.__path__`` needs no edit to any shared file. (A name offered
    by two path entries cannot appear twice: `pkgutil.iter_modules` yields each name once.)"""
    return tuple(
        sorted(
            m.name
            for m in pkgutil.iter_modules(package.__path__)
            if m.name not in skip and not m.name.startswith("_")
        )
    )


#: Every section module: each public module of this package, found by convention. A new
#: `[section]` is a new file here: its `@declare("<name>")` maps the section name to its
#: dataclass in `_docs.SECTION_CLASSES`, from which `Config` assembles its fields and
#: `ddflow.config` re-exports the class (r-extension-contract). `config --explain` follows
#: `Config`'s field order, which is alphabetical by section name.
SECTION_NAMES = module_names(sys.modules[__name__])

SECTION_MODULES = tuple(importlib.import_module(f"{__name__}.{n}") for n in SECTION_NAMES)
