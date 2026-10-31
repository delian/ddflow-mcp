"""Worktree and branch cleanup — landing work that was left in flight.

The situation this exists for: after a run of interrupted sessions a repository
accumulates worktrees and branches in every state — some fully merged and just not
removed, some carrying commits nobody landed, some holding uncommitted edits, some
belonging to items the queue believes are finished. Left alone they are indistinguishable
from each other, and the safe-looking action (delete the lot) is the one that loses work.

So this module **classifies before it acts**, and the classification is the product.
The former ``ddflow/services/cleanup.py`` is split into submodules that stay under 30k
chars each (rule r-small-files); the public API is unchanged: every name the old module
exported is re-exported here, so ``from ddflow.services import cleanup as CL`` and
``from ddflow.services.cleanup import dispose_tree`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. That keeps two invariants a one-file module had and a package must keep —
monkeypatch (``monkeypatch.setattr(CL, name, ...)`` re-binds the name on THIS module exactly
as before) and the layering ratchet (an eager ``from .dispose import ...`` here, plus its
late-bound siblings, would read as a latent parent<->child cycle).
"""

from __future__ import annotations

import importlib
from typing import Any

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # model
    "Disposal": "model",
    "Plan": "model",
    "Stranded": "model",
    "TreeState": "model",
    "_Protected": "model",
    "_key": "model",
    "stranded_text": "model",
    # stranded
    "_clone_of": "stranded",
    "_own_released": "stranded",
    "_own_released_branches": "stranded",
    "_protected": "stranded",
    "removed_with_live_lease": "stranded",
    "stranded_leases": "stranded",
    # dispose
    "_drop_branch": "dispose",
    "_finish_stamps": "dispose",
    "dispose_finished": "dispose",
    "dispose_tree": "dispose",
    "record_item_removed": "dispose",
    "record_removed": "dispose",
    "stands_in": "dispose",
    "sweep_finished": "dispose",
    # survey
    "Path": "survey",
    "_classify": "survey",
    "_claimed_by": "survey",
    "_measure": "survey",
    "_ours": "survey",
    "_owner_alive": "survey",
    "_scratch_row": "survey",
    "_stale_branch": "survey",
    "_under_sys_tmp": "survey",
    "landed_leftovers_only": "survey",
    "our_prefixes": "survey",
    "our_trees": "survey",
    "proved_leftover_tip": "survey",
    "scratch_leftovers": "survey",
    "survey": "survey",
    "unclaimed_trees": "survey",
    # apply
    "_pruned_scratch": "apply",
    "_registered": "apply",
    "_remove_planned": "apply",
    "apply": "apply",
}

_MODULES: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    sub = _SUBMODULE_OF.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = _MODULES.get(sub)
    if mod is None:
        mod = importlib.import_module(f"{__name__}.{sub}")
        _MODULES[sub] = mod
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_SUBMODULE_OF))
