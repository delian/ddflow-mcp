"""E2 emit: what a hook prints, one of a closed list of named variants.

The variant -> function table lives in ``hookio`` (one definition, r-reuse-core); this module
names them by what they print, so a descriptor says ``emitter = "json_context"`` instead of an
agent's name.
"""

from __future__ import annotations

from ..hookio import EMIT_VARIANTS
from .safety import EngineError

VARIANTS = frozenset(EMIT_VARIANTS)


def variant_for(emitter: str) -> str:
    """The variant a descriptor's ``[hooks] emitter`` names; the descriptor value IS the
    variant, so this only checks it is in the closed list."""
    if not isinstance(emitter, str):
        raise EngineError(f"emitter must be a string, got {type(emitter).__name__}")
    if emitter not in VARIANTS:
        raise EngineError(f"unknown emitter {emitter!r}; variants: {sorted(VARIANTS)}")
    return emitter


def render(variant: str, event: str, text: str, inject: bool, native: str = "") -> str:
    """The exact stdout for ``text`` under ``variant``; ``EngineError`` for a name outside the set."""
    if not isinstance(variant, str) or variant not in VARIANTS:
        raise EngineError(f"unknown emit variant {variant!r}; closed list: {sorted(VARIANTS)}")
    return EMIT_VARIANTS[variant](event, text, inject, native)
