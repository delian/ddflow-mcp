"""E2 emit: what a hook prints, one of a closed list of named variants.

The functions stay in ``hookio.EMITTERS``; this names them by what they print, so a
descriptor says ``emit = "json_context"`` instead of an agent's name.
"""

from __future__ import annotations

from ..hookio import EMITTERS
from .safety import EngineError

#: variant -> the shipped emitter id that implements it
_IMPL = {
    "plain": "claude",
    "json_context": "gemini",
    "copilot": "copilot",
    "cursor": "cursor",
    "cline": "cline",
    "antigravity": "antigravity",
    "silent": "cascade",
}
VARIANTS = frozenset(_IMPL)
#: shipped emitter id -> variant (the compatibility reading of today's ``[hooks] emitter``)
_LEGACY = {
    "claude": "plain",
    "codex": "plain",
    "gemini": "json_context",
    "vscode": "json_context",
    "goose": "silent",
    **{v: k for k, v in _IMPL.items() if k not in ("plain", "json_context")},
}


def variant_for(emitter: str) -> str:
    """The variant a descriptor's emitter id means; a variant name is its own."""
    if emitter in VARIANTS:
        return emitter
    if emitter in _LEGACY:
        return _LEGACY[emitter]
    raise EngineError(f"unknown emitter {emitter!r}; variants: {sorted(VARIANTS)}")


def render(variant: str, event: str, text: str, inject: bool, native: str = "") -> str:
    """The exact stdout for ``text`` under ``variant``; ``EngineError`` for a name outside the set."""
    if variant not in VARIANTS:
        raise EngineError(f"unknown emit variant {variant!r}; closed list: {sorted(VARIANTS)}")
    return EMITTERS[_IMPL[variant]](event, text, inject, native)
