"""The gate pipeline: ask what is next, run it, record it, skip it, prove it can fail.

This family was `ddflow/api/gates.py` and is now a package (r-small-files): the module
was split into ``_timeout.py``, ``_tree.py``, ``_query.py``, ``_run.py``, ``_vet.py`` and
``record.py``, each under 30k chars, with the public API re-exported here unchanged.

Two rules are worth reading before changing anything:

* **Order is enforced but not by default.** ``gates.enforce_order`` is ``warn``, and a
  recording out of order appends a ``gate.out_of_order`` event either way. Reported rather
  than refused because some interleaving is legitimate, and a tool that blocks on every
  harmless reordering gets ``--force``d on reflex. The event exists so that whether
  ``warn`` should become ``block`` has evidence behind it.
* **A human gate is a REFUSAL (3), never a failure (1).** Nothing went wrong; the caller
  is simply not the party who can clear it. An agent branching on exit codes needs to
  tell "this is broken" from "this is not yours to do".
"""

from __future__ import annotations

from .._base import _load as _load
from ._query import list_gates as list_gates
from ._query import status as status
from ._query import sweep_dead_runs as sweep_dead_runs
from ._query import verify as verify
from ._run import OUTCOME_EXIT as OUTCOME_EXIT
from ._run import _note_logged as _note_logged
from ._run import run as run
from ._timeout import _with_run_timeout as _with_run_timeout
from ._tree import item_tree as item_tree
from ._vet import Evidence as Evidence
from .record import record as record

__all__ = [
    "OUTCOME_EXIT",
    "Evidence",
    "item_tree",
    "list_gates",
    "record",
    "run",
    "status",
    "sweep_dead_runs",
    "verify",
]
