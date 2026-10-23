"""The hidden aliases of the family surface (MTD-aliases, D-mcp-tool-families).

Every tool name that is not a hot single is an old name of ``<family> action=<verb>``. The
old names stay callable (they are the members `family_schema.route` runs), are never advertised
by a family profile, and are said ONCE per session (`registry.Notices`), as every rename is
(D-compat). One `Alias` per name, generated from `families.ALIASES`; nothing is listed here.

``removed_in`` is the D-compat floor (1.0): the decision's two-release window is the least a
profile may keep them, and removing earlier needs D-compat relaxed first (MTD-sunset).
"""

from __future__ import annotations

from ..registry import Alias
from .families import ALIASES as _PLACED

__all__ = ("ALIASES", "SINCE", "alias_for")

#: The release that ships the family surface.
SINCE = "0.2.8"

#: Old tool name -> its `Alias`; ``new`` reads as the call that replaces it.
ALIASES: dict[str, Alias] = {
    a.old: Alias("tool", a.old, f"{a.family} action={a.action}", SINCE) for a in _PLACED
}


def alias_for(name: str) -> Alias | None:
    """The alias to note when ``name`` is called, or None: a hot single, an unknown name, or a
    self-named family tool (``ddflow_doctor``), whose name is still the family's own."""
    alias = ALIASES.get(name)
    if alias is None or alias.new.startswith(name + " "):
        return None
    return alias
