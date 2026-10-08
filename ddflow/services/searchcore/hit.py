"""The one record a search source yields, and the registry of sources.

A source says how to enumerate its rows -- id, kind, state, date, owner, phase and the text
that is matched -- and the engine ranks, filters and cuts them the same way for every source
(D-unify 3: `ddflow search` is the one search, selecting sources with `--kind`; `recall` is its budgeted view).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Hit:
    """One searchable row. `text` is matched and cut for the snippet; the rest filters."""

    kind: str
    id: str
    state: str
    date: str
    owner: str
    phase: str
    text: str


class SearchSource(Protocol):
    """How a source enumerates its rows for one request.

    `kinds` is the set of kinds the request asked for; a source yields only rows of kinds it
    owns that are in it. `ctx` is whatever the caller shares between sources (state, events,
    config); the engine does not look inside.
    """

    name: str
    kinds: tuple[str, ...]

    def hits(self, ctx: Any, kinds: set[str]) -> list[Hit]: ...


@dataclass(frozen=True)
class FuncSource:
    """A source built from a plain function."""

    name: str
    kinds: tuple[str, ...]
    fn: Callable[[Any, set[str]], list[Hit]]

    def hits(self, ctx: Any, kinds: set[str]) -> list[Hit]:
        return self.fn(ctx, kinds) if kinds & set(self.kinds) else []


_REGISTRY: dict[str, SearchSource] = {}


def register(source: SearchSource) -> SearchSource:
    """Add `source` (replacing one of the same name, so a reload is harmless)."""
    _REGISTRY[source.name] = source
    return source


def registered() -> list[SearchSource]:
    """Every source, in registration order."""
    return list(_REGISTRY.values())


def gather(ctx: Any, kinds: set[str]) -> list[Hit]:
    """The rows of every registered source that owns one of `kinds`."""
    out: list[Hit] = []
    for src in registered():
        if kinds & set(src.kinds):
            out += src.hits(ctx, kinds)
    return out
