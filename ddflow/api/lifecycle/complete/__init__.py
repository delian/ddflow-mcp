"""`complete`, `abandon`, `remove`, `block`, `unblock`: ending an item's life in the queue.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here.

This package is the former ``ddflow/api/lifecycle/complete.py``, split into submodules that
stay under 20 KB each (rule r-small-files, decision D-small-files-30k): ``finish``
(`complete` and the umbrella climb), ``approval`` (the `--force` / `--approval` policy),
``checks`` (phase suite, failed reviews, umbrellas), ``closing`` (bug closing, phase-end
self-assessment), ``dispose`` (the finished item's tree and jobs), ``abandoning``, and
``queue_ops`` (`remove`, `block`, `unblock`). Every name the old module defined is
re-exported LAZILY (``core._reexport``), so ``from ddflow.api.lifecycle.complete import X``
binds what it bound before. A test that REPLACES a name patches the submodule that defines it
(e.g. ``complete.finish._dispose``).
"""

from __future__ import annotations

from ddflow.core import _reexport

_SUBMODULE_OF = {
    "abandon": "abandoning",
    "_abandon_refused": "abandoning",
    "_rename": "abandoning",
    "_approved_force": "approval",
    "_ask_hint": "approval",
    "_auto_complete_unavailable": "approval",
    "_only_unavailable": "approval",
    "_override_by_approval": "approval",
    "_person_force": "approval",
    "_failed_review": "checks",
    "_na_map": "checks",
    "_phase_suite": "checks",
    "_refuted_extra": "checks",
    "_umbrella_children": "checks",
    "_close_arg_refusal": "closing",
    "_close_bugs": "closing",
    "_phase_end_self_assessment": "closing",
    "_dispose": "dispose",
    "complete": "finish",
    "fold": "finish",
    "_complete_umbrellas_above": "finish",
    "_closing_tail": "finish",
    "block": "queue_ops",
    "remove": "queue_ops",
    "unblock": "queue_ops",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
