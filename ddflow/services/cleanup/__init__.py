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

from ddflow.core import _reexport

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
    # plan (survey/classification)
    "Path": "plan",
    "_classify": "plan",
    "_claimed_by": "plan",
    "_measure": "plan",
    "_ours": "plan",
    "_owner_alive": "plan",
    "_scratch_row": "plan",
    "_stale_branch": "plan",
    "_under_sys_tmp": "plan",
    "landed_leftovers_only": "plan",
    "our_prefixes": "plan",
    "our_trees": "plan",
    "proved_leftover_tip": "plan",
    "scratch_leftovers": "plan",
    "survey": "plan",
    "unclaimed_trees": "plan",
    # actions (apply)
    "_pruned_scratch": "actions",
    "_registered": "actions",
    "_remove_planned": "actions",
    "apply": "actions",
}

__all__: list[str] = list(_SUBMODULE_OF)  # star-import re-exports exactly the public API

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
