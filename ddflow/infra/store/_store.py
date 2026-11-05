"""The `Store` class — the SQLite projection, composed from its three halves."""

from __future__ import annotations

from ._projection import _ProjectionMixin
from ._schema import _SchemaMixin
from ._search import _SearchMixin


class Store(_SchemaMixin, _ProjectionMixin, _SearchMixin):
    """The SQLite projection — a cache and a search index, never a source of truth.

    See the package docstring in ``__init__.py`` for the contract this enforces.
    """
