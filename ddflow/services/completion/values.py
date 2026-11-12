"""Value completion: a declared or inferred kind -> candidate strings.

The completion engine asks "for an argument of kind ``kind`` and the word typed so far,
what may the value be?" and this module answers, keyed ONLY by the kind string -- never by
command, option or choice (the GENERIC rule). ``enum`` is NOT here: the argument's own
``choices`` are the enum candidates, carried on the tree node, so the engine reads them
there; the kinds this module completes are the ones a choices list cannot express.

The non-id sources (gate names, agent names) live behind lazy imports, so this module
stays light for the id path and a heavy source is paid only on the TAB that needs it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import completion_ids as CI


def enum(choices: list | tuple) -> list[str]:
    """The enum candidates are the argument's own choices: the engine reads them there."""
    return [str(c) for c in choices]


def complete(
    kind: str,
    prefix: str = "",
    *,
    index: str | None = None,
    state: str | None = None,
    root: str | None = None,
) -> list[str]:
    """Candidates for ``prefix`` of an argument whose kind is ``kind``.

    ``kind`` is the tree's ``"kind"`` value: ``id``, ``id:<record kind>``, ``gate``,
    ``state``, ``agent``, ``kind``, ``path``, ``dir`` or ``none``. ``index`` is the newest
    ``.ddflow/index.db-*`` file (see `completion_ids.find_index`).
    """
    if kind in ("", "none", "enum"):
        return []
    if kind == "id":
        return CI.complete_ids(prefix, index=index)
    if kind.startswith("id:"):
        return CI.complete_ids(prefix, kind[3:], state, index=index)
    if kind == "phase":  # the short spelling `normalize` canonicalizes, but not every tree built it
        return CI.phases(prefix, index=index)
    if kind == "state":
        return _prefixed(CI.states(index=index), prefix)
    if kind == "kind":
        return _prefixed(CI.kinds(index=index), prefix)
    if kind == "agent":
        return _prefixed(_agent_names(), prefix)
    if kind == "gate":
        return _prefixed(_gate_names(root), prefix)
    if kind == "path":
        return _paths(prefix, dirs_only=False)
    if kind == "dir":
        return _paths(prefix, dirs_only=True)
    return []


#: How many file/directory matches one TAB offers (a listing must stay bounded).
_PATH_MAX = 200
#: How many filesystem entries a path TAB scans before sorting (bounded work).
_PATH_SCAN = 2000


def _prefixed(names: list[str], prefix: str) -> list[str]:
    return [n for n in names if n.startswith(prefix)]


def _paths(prefix: str, *, dirs_only: bool) -> list[str]:
    """Paths under ``prefix`` from the filesystem (``glob``): a directory kind offers
    directories; a path kind offers files AND the directories you traverse to reach them."""
    import glob as _g
    import os as _os

    if any(c in prefix for c in ("\x00", "\n", "\r")):
        return []
    pat = _g.escape(prefix) + "*"
    try:
        from itertools import islice as _islice

        found: list[str] = []
        for f in _islice(_g.iglob(pat), _PATH_SCAN):
            if dirs_only and not _os.path.isdir(f):
                continue  # a directory kind filters as it scans, before the cap drops it
            found.append(f)
            if len(found) >= _PATH_MAX:
                break
        return sorted(found)
    except (OSError, ValueError):
        return []


def _agent_names() -> list[str]:
    from ...services import harnessreg  # heavy: only on an `agent` TAB

    return harnessreg.ids()


def _gate_names(root: str | None) -> list[str]:
    from ...config import Config  # heavy: only on a `gate` TAB
    from ...services.gates import defs as GD

    repo = Path(root) if root is not None else Path(os.getcwd())
    cfg = Config.load(repo)
    return sorted(GD.load_gates(repo, cfg))
