"""E2 emit: what a hook prints, one of a closed list of named variants.

The functions stay in ``hookio.EMITTERS``; this names them by what they print, so a
descriptor says ``emit = "json_context"`` instead of an agent's name.
"""

from __future__ import annotations

from ..hookio import (
    EMITTERS,
    _antigravity,
    _cline,
    _copilot,
    _cursor,
    _json_context,
    _plain,
    _silent,
)
from .safety import EngineError

#: variant -> the hookio function that prints it (one definition, r-reuse-core)
_IMPL = {
    "plain": _plain,
    "json_context": _json_context,
    "additional_context": _copilot,
    "additional_context_snake": _cursor,
    "context_modification": _cline,
    "empty_json": _antigravity,
    "silent": _silent,
}
VARIANTS = frozenset(_IMPL)


def variant_for(emitter: str) -> str:
    """The variant a descriptor's ``[hooks] emitter`` id means (an id's function decides, so no
    agent is named here); a variant name is its own."""
    if emitter in VARIANTS:
        return emitter
    fn = EMITTERS.get(emitter)
    for name, impl in _IMPL.items():
        if impl is fn:
            return name
    raise EngineError(f"unknown emitter {emitter!r}; variants: {sorted(VARIANTS)}")


def render(variant: str, event: str, text: str, inject: bool, native: str = "") -> str:
    """The exact stdout for ``text`` under ``variant``; ``EngineError`` for a name outside the set."""
    if variant not in VARIANTS:
        raise EngineError(f"unknown emit variant {variant!r}; closed list: {sorted(VARIANTS)}")
    return _IMPL[variant](event, text, inject, native)
