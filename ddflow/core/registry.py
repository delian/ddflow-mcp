"""Compat re-export: the one `Registry[T]` lives in `ddflow.adapters.registry`.

`ddflow.adapters` is the bottom of the layer contract and imports no other ddflow layer, so
the registry it needs sits there and `core` (which may import it) re-exports the same
objects: there is ONE implementation (r-reuse-core). Import from here or from there.
"""

from __future__ import annotations

from ..adapters.registry import (
    DuplicateName,
    Lazy,
    Registry,
    RegistryError,
    UnknownName,
)

__all__ = ["DuplicateName", "Lazy", "Registry", "RegistryError", "UnknownName"]
