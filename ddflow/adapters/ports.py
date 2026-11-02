"""The shared base of the adapter model: what a **port** is, and how ports are found.

A **port** is a `Protocol` (structural, no base class to inherit, no import the other way)
marked `@port`; its **default adapter** sits beside it, and its **fake** is the shipped
stand-in in `ddflow/adapters/fakes/`. Every family is its OWN module, so adding one edits no
shared file (ADP-modular-registry, r-small-files):

* `ddflow/adapters/<family>.py` -- the port, its default adapter, its registry if it has
  several adapters;
* `ddflow/adapters/fakes/<family>.py` -- `@fake class Fake<Port>`;
* `tests/test_adapter_<family>.py` -- the conformance cases;
* `docs/ddflow/adapters/<family>.md` -- the family's page.

This module holds only the marker `port` and the discovery helpers; it imports the standard
library alone, and no family. The pattern and the add-a-port steps are in
`docs/ddflow/adapters/README.md`.

Naming: a port is the family's noun with no suffix, so it reads as a type in a signature. A single-adapter family has exactly ONE implementation in its home -- a
second is a bug, not a registerable alternative. A family with SEVERAL adapters registers
them in a `ddflow.adapters.registry.Registry[T]` keyed by the adapter's `name`.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import TypeVar

_T = TypeVar("_T")

#: The attribute `@port` sets; discovery reads it, so no list of ports exists anywhere.
MARK = "__ddflow_port__"
#: Modules of a package that are plumbing, not families.
PLUMBING = frozenset({"ports", "registry", "fakes"})


def port(cls: _T) -> _T:
    """Mark a `Protocol` class as an adapter port. Changes nothing else about it."""
    setattr(cls, MARK, True)
    return cls


def module_names(package: ModuleType, skip: frozenset[str] = PLUMBING) -> list[str]:
    """The submodule names of `package`, sorted: the one deterministic order (no hash order)."""
    return sorted(m.name for m in pkgutil.iter_modules(package.__path__) if m.name not in skip)


def discover(package: ModuleType, skip: frozenset[str] = PLUMBING) -> dict[str, type]:
    """Every `@port` class defined in a module of `package`, by class name, in sorted
    module-name order. Importing a family module is the only side effect."""
    found: dict[str, type] = {}
    for name in module_names(package, skip):
        module = importlib.import_module(f"{package.__name__}.{name}")
        for _, cls in sorted(vars(module).items()):
            if (
                isinstance(cls, type)
                and cls.__dict__.get(MARK)
                and cls.__module__ == module.__name__
            ):
                if found.get(cls.__name__) is cls:
                    continue  # an alias of a port already seen
                if cls.__name__ in found:
                    raise ValueError(
                        f"two ports named {cls.__name__}: {found[cls.__name__].__module__} and "
                        f"{module.__name__}; a port name is its key, rename one"
                    )
                found[cls.__name__] = cls
    return found
