"""Work actually done, and loops that never finish it.

This package is the former ``ddflow/core/progress.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The names the old module defined — its
public API and the private names the tests patch — are re-exported here from
``_SUBMODULE_OF``, so ``from ddflow.core.progress import new_reports_line`` and
``from ddflow.core import progress as PR`` bind the defined names exactly as before.
Names the old module only imported at top level were not part of that surface and are
not re-exported.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for, and keeping it lazy (rather than an eager ``from .work import ...``)
avoids a parent<->child cycle in ``test_no_mutually_importing_pair``.
``monkeypatch.setattr(PR, name, ...)`` re-binds the name on THIS package; a peer that
resolves the same name through its own submodule global (``loops.detect`` uses ``work``
via ``from .tracking import work``) is not seen by such a patch — a difference from the
old single namespace no call site relies on.
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and test-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # models
    "Attempt": "models",
    "BOOKKEEPING_KINDS": "models",
    "ItemWork": "models",
    "LoopFinding": "models",
    "PROGRESS_KINDS": "models",
    "REVIEW_GATES": "models",
    "_epoch": "models",
    "epoch": "models",
    # tracking
    "_absorb": "tracking",
    "work": "tracking",
    # loops
    "_duplicate_work": "loops",
    "_gate_flapping": "loops",
    "_is_removed": "loops",
    "_no_progress": "loops",
    "_reopened": "loops",
    "_repeat_claims": "loops",
    "_repeated_failures": "loops",
    "_static_cycles": "loops",
    "_waits_on": "loops",
    "detect": "loops",
    # tallies
    "STATE_ORDER": "tallies",
    "Tally": "tallies",
    "phase_of": "tallies",
    "phase_tally": "tallies",
    "state_counts": "tallies",
    "tally": "tallies",
    # board
    "BoardSection": "board",
    "board_rows": "board",
    "depth": "board",
    "nested": "board",
    "new_reports_line": "board",
    "unphased": "board",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
