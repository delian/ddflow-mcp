"""Shipped fakes: one module per family, one `@fake class Fake<Port>` in it (D-adapters-answers-2).

`FAKES` maps a port's name to its fake class. It is filled by importing every module of this
package in sorted name order (`collect`), so a new fake is a new file and edits no shared one;
importing a fake module registers nothing else. `tests/test_ports_contract.py` requires every
port to have one (a baseline of ports still without one only shrinks) and runs the family's
conformance cases against the default adapter and the fake. Fakes are shipped, not test-only,
so a downstream user can build against a port without the real dependency.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import TypeVar

from ..ports import module_names
from ..registry import Registry

_T = TypeVar("_T")

#: The attribute `@fake` sets: the port's name (the class name without `Fake`).
MARK = "__ddflow_fake__"


def fake(cls: _T) -> _T:
    """Mark a `Fake<Port>` class as the fake of the port named without its prefix."""
    setattr(cls, MARK, cls.__name__.removeprefix("Fake"))
    return cls


def collect(package: ModuleType) -> dict[str, type]:
    """Every `@fake` class defined in a module of `package`, by port name, in sorted module
    order. Importing the modules is the only side effect."""
    found: dict[str, type] = {}
    for name in module_names(package, frozenset()):
        module = importlib.import_module(f"{package.__name__}.{name}")
        for _, cls in sorted(vars(module).items()):
            if (
                isinstance(cls, type)
                and cls.__dict__.get(MARK)
                and cls.__module__ == module.__name__
            ):
                port = cls.__dict__[MARK]
                if port in found:
                    raise ValueError(
                        f"two fakes for {port}: {found[port].__module__} and {module.__name__}"
                    )
                found[port] = cls
    return found


FAKES: Registry[type] = Registry("fake", key=lambda cls: cls.__dict__[MARK])
for _cls in collect(importlib.import_module(__name__)).values():
    FAKES.register(_cls)
