"""``registry:<name>``: text a named generator produces, for engines that are code.

A module registers ``GENERATORS`` entries (``name -> fn(Context) -> str``); a section names one.
RH-docs-engine registers its README-region assembly here instead of running a second generator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.registry import Registry
from .sources import Context, SourceError, SourceKind, register

Generator = Callable[[Context], str]
GENERATORS: Registry[tuple[str, Generator]] = Registry("document generator", key=lambda g: g[0])


def add(name: str, fn: Generator) -> None:
    GENERATORS.register((name, fn))


def _check(arg: str) -> str:
    if not arg:
        raise SourceError("source 'registry' needs a name: registry:<name>")
    return arg


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    entry = GENERATORS.get(arg)
    if entry is None:
        raise SourceError(
            f"no document generator {arg!r}. Known: {', '.join(GENERATORS) or '(none)'}"
        )
    return {"text": entry[1](ctx)}


register(SourceKind("registry", _check, _data))
