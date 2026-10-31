"""Work actually done, and loops that never finish it.

This package is the former ``ddflow/core/progress.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.core.progress
import new_reports_line`` and ``from ddflow.core import progress as PR`` bind exactly
what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for. That keeps two invariants a one-file module had and a package must
keep — monkeypatch (``monkeypatch.setattr(PR, name, ...)`` re-binds the name on THIS
module exactly as before) and the layering ratchet (``test_no_mutually_importing_pair``
would read an eager ``from .work import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

import importlib
from typing import Any

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
