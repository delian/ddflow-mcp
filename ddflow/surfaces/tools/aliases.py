"""The hidden aliases of the family surface (MTD-aliases, D-mcp-tool-families).

Every tool name that is not a hot single or a self-named family tool is an old name of ``<family> action=<verb>``. The
old names stay callable (they are the members `family_schema.route` runs), are never advertised
by a family profile, and are to be said ONCE per session (`registry.Notices`), as every rename is
(D-compat). One `Alias` per name, generated from `families.ALIASES`; nothing is listed here. The
engine says them only on a connection whose profile advertises the families (a flat connection
still advertises these names, so they are not old there): MTD-profiles calls `alias_for` there.

``removed_in`` is the D-compat floor (1.0): the decision's two-release window is the least a
profile may keep them, and removing earlier needs D-compat relaxed first (MTD-sunset).
"""

from __future__ import annotations

from ..registry import Alias
from .families import ALIASES as _PLACED

__all__ = ("ALIASES", "REMOVED_IN", "SINCE", "alias_for")

#: The release that ships the family surface.
SINCE = "0.2.8"

#: The D-compat floor: an old name is kept until 1.0 at least.
REMOVED_IN = "1.0"

#: Old tool name -> its `Alias`; ``new`` reads as the call that replaces it. The two self-named
#: family tools (``ddflow_doctor``, ``ddflow_workflow``) are not here: they are current tools.
ALIASES: dict[str, Alias] = {
    a.old: Alias("tool", a.old, f"{a.family} action={a.action}", since=SINCE, removed_in=REMOVED_IN)
    for a in _PLACED
    if a.old != a.family
}


def alias_for(name: str) -> Alias | None:
    """The alias to note when ``name`` is called, or None (a hot single, a self-named family
    tool, an unknown name)."""
    return ALIASES.get(name)
