"""The records the fold produces: the item states, every record dataclass, and `State`.

This package is the former ``ddflow/core/records.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.core.records
import State`` and ``from ddflow.core import records`` bind exactly what they bound
before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for. That keeps two invariants a one-file module had and a package must
keep — monkeypatch (``monkeypatch.setattr(records.State, name, ...)`` re-binds the name
on THIS module exactly as before) and the layering ratchet
(``test_no_mutually_importing_pair`` would read an eager ``from .gates import ...``
here as a latent parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core._reexport import reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # gates
    "ABANDONED": "gates",
    "BLOCKED": "gates",
    "DONE": "gates",
    "GATE_OUTCOMES": "gates",
    "GateOutcome": "gates",
    "GateRecord": "gates",
    "GateRun": "gates",
    "OPEN": "gates",
    "OUTCOME_MARK": "gates",
    "REVIEW": "gates",
    "RUNNING": "gates",
    "_EXITS": "gates",
    "_MARKS": "gates",
    # items
    "DEFAULT_LEASE_TTL_S": "items",
    "Item": "items",
    "Lease": "items",
    "PullRequest": "items",
    "WORK_KINDS": "items",
    "_LEASE_FIELDS": "items",
    "_claim": "items",
    "_clashing": "items",
    "_other_hand": "items",
    "_overlaps": "items",
    "_span": "items",
    "work_kind": "items",
    # content
    "ADD_RELATIONS": "content",
    "Bug": "content",
    "Decision": "content",
    "FoldProblem": "content",
    "Job": "content",
    "Lesson": "content",
    "LINK_RELATIONS": "content",
    "Memory": "content",
    "RecordLinks": "content",
    "Release": "content",
    "ResearchNote": "content",
    "SIGHTINGS_THAT_CONFIRM": "content",
    "Schedule": "content",
    "Session": "content",
    "contested_summary": "content",
    # state
    "State": "state",
    "record_pools": "state",
}

reexport(globals(), _SUBMODULE_OF, __name__)
