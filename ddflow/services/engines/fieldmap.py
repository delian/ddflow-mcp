"""E1 fieldmap: where a dialect keeps each fact, as dotted paths, first non-empty wins.

The type and the shipped dialects stay in ``hookio`` (one definition, r-reuse-core); this
module is the data entry point (``[hooks.fields]`` -> ``FieldMap``) and the extraction.
"""

from __future__ import annotations

from typing import Any

from ..hookio import FieldMap, _dig, _first_text
from .safety import DEFAULT, EngineError, Limits, check_path, expect_keys

__all__ = ["FIELDS", "FieldMap", "dig", "extract", "from_table"]

FIELDS = ("session", "prompt", "source", "model", "cwd", "tool")
dig = _dig


def from_table(table: object, where: str, limits: Limits = DEFAULT) -> FieldMap:
    """Validate a ``[hooks.fields]`` table; a field left out keeps FieldMap's default."""
    t = expect_keys(table, set(FIELDS), where)
    kw: dict[str, tuple[str, ...]] = {}
    for name, paths in t.items():
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise EngineError(f"{where}.{name}: expected a list of paths")
        kw[name] = tuple(check_path(p, f"{where}.{name}", limits) for p in paths)
    return FieldMap(**kw)


def extract(fm: FieldMap, raw: Any, limits: Limits = DEFAULT) -> dict[str, str]:
    """Every field of ``fm`` read from the parsed hook stdin; ``""`` when absent."""
    if not isinstance(raw, dict):
        raw = {}
    return {n: (_first_text(raw, getattr(fm, n)) or "")[: limits.max_value_chars] for n in FIELDS}
