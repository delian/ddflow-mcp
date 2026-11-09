"""Shared lazy name re-export for the split ``<30k`` packages.

Each former one-file module split into a package keeps its public API by
re-exporting names from submodules. This helper makes that re-export LAZY:
the package imports no submodule until a name is asked for, which preserves
two invariants a one-file module had and a package must keep — monkeypatch
(``monkeypatch.setattr(pkg, name, ...)`` re-binds the name on THIS module) and
the layering ratchet (an eager ``from .sub import ...`` here would read as a
latent parent<->child cycle).

A star-import reads the module ``__dict__`` (or ``__all__`` when defined), never
``__dir__`` — so this helper also installs ``__all__``: the package's real public names plus
the public keys of the map, so ``from pkg import *`` resolves the lazy names too. It is the
package's final name-defining statement; a package that wants a narrower ``__all__`` sets its
own AFTER this call.

Installed from a package ``__init__.py`` under the PRIVATE module name, so the helper
itself never becomes a public name of the package (or of the ``__all__`` it writes)::

    from ddflow.core import _reexport
    ...
    _reexport.reexport(globals(), _SUBMODULE_OF, __name__)
    del _reexport
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from typing import Any


def reexport(here: dict, submodule_of: Mapping[str, str], package: str) -> None:
    """Install lazy ``__getattr__``/``__dir__`` on `here` (a module dict).

    `submodule_of` maps a public (or patch-covered private) name to the sibling
    submodule that defines it. ``__getattr__`` imports that submodule on first
    use and returns the attribute; ``__dir__`` lists the package's real
    attributes plus everything the map would resolve.
    """
    cache: dict[str, Any] = {}

    def __getattr__(name: str) -> Any:
        sub = submodule_of.get(name)
        if sub is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        mod = cache.get(sub)
        if mod is None:
            fullname = f"{package}.{sub}"
            # A halted import leaves ``sys.modules[fullname] = None``, and
            # ``importlib.import_module`` then re-raises ``ModuleNotFoundError`` on every
            # later access instead of re-importing -- so a transient halt makes a lazy name
            # permanently broken (B7e585f9e5d: ``ddflow.services.ci.main`` crashed
            # ``check_after_merge``). Drop the sentinel (or nothing) so the retry re-imports.
            if sys.modules.get(fullname) is None:
                sys.modules.pop(fullname, None)
            mod = importlib.import_module(fullname)
            cache[sub] = mod
        return getattr(mod, name)

    def __dir__() -> list[str]:
        return sorted(set(here) | set(submodule_of))

    here["__getattr__"] = __getattr__
    here["__dir__"] = __dir__
    # Star-import reads __dict__ or __all__, never __dir__: name every public name the
    # module exposes (real globals plus the public keys of the map) so `from pkg import *`
    # resolves the lazy ones through __getattr__. Recomputed every import, so an
    # `importlib.reload` never keeps a stale list. Private keys stay out, as an un-__all__
    # star-import skips underscore names too.
    here["__all__"] = sorted(
        {n for n in here if not n.startswith("_")}
        | {n for n in submodule_of if not n.startswith("_")}
    )
