"""Dependency resolution, readiness and parallel fan-out.

The scheduler answers exactly one question: *given the current state, which items may
start RIGHT NOW, and for each item that may not, why not?* The "why not" half is not a
nicety — an agent told only "nothing to do" will invent work, whereas an agent told
"T3 waits on T2, which agent-b holds until 14:05" waits correctly or picks another
phase.

Two independent reasons block an item, and they are reported separately because the
remedies differ:

* **Dependency** — ``needs`` names an item that is not done. Remedy: wait, or do that.
* **Conflict** — another agent holds a live lease whose file globs overlap ours.
  Remedy: pick a non-overlapping item; the whole point of reporting the non-overlapping
  ones is that re-ordering beats blocking.

Glob overlap is deliberately over-eager. A false positive costs one unnecessary
re-order; a false negative costs two agents editing one file and one of them silently
losing their work. The asymmetry is not close, so the comparison errs toward "yes".
"""

from __future__ import annotations

from ...config import Config as Config
from .. import flowcontrol as FC  # noqa: F401  # re-exported as the historical ``schedule.FC`` alias
from ..admission import Clash as Clash
from ..admission import conflicts as conflicts
from ..admission import glob_conflict as glob_conflict
from ..admission import is_shared as is_shared
from ..admission import shared_globs as shared_globs
from ..autoorder import is_decide as is_decide
from ..flow import FEATURE as FEATURE
from ..flow import branch_kind as branch_kind
from ..flow import stack_base as stack_base
from ..flow import unknown_line as unknown_line
from ..flowparams import params as params
from ..globs import inside as path_in_glob  # noqa: F401  # re-exported under its public name
from ..globs import overlap as globs_overlap  # noqa: F401  # re-exported as ``schedule.globs_overlap``
from ..graph import closure as closure
from ..graph import find_cycles as find_cycles
from ..graph import longest_chains as longest_chains
from ..model import BLOCKED as BLOCKED
from ..model import DONE as DONE
from ..model import REVIEW as REVIEW
from ..model import RUNNING as RUNNING
from ..model import Item as Item
from ..model import Lease as Lease
from ..model import State as State
from ..records import SIGHTINGS_THAT_CONFIRM as SIGHTINGS_THAT_CONFIRM
from ._chain import _chain_scope as _chain_scope
from ._chain import chain_cycles as chain_cycles
from ._chain import critical_path as critical_path
from ._filters import FILTERS as FILTERS
from ._filters import KEYS as KEYS
from ._filters import LEASE_FILTERS as LEASE_FILTERS
from ._filters import ORDER as ORDER
from ._filters import Filter as Filter
from ._filters import Key as Key
from ._filters import PlanCtx as PlanCtx
from ._filters import _f_deps as _f_deps
from ._filters import _f_globs as _f_globs
from ._filters import _f_lease as _f_lease
from ._filters import _f_resources as _f_resources
from ._filters import item_blocker as item_blocker
from ._filters import order_key as order_key
from ._filters import register_filter as register_filter
from ._filters import register_key as register_key
from ._parallel import LIMIT_UNAVAILABLE as LIMIT_UNAVAILABLE
from ._parallel import _cap_words as _cap_words
from ._parallel import _words as _words
from ._parallel import parallel_line as parallel_line
from ._plan import _cut_ready as _cut_ready
from ._plan import plan as plan
from ._readiness import Blocked as Blocked
from ._readiness import Plan as Plan
from ._readiness import Unpickable as Unpickable
from ._readiness import _docs_only_exempt as _docs_only_exempt
from ._readiness import _glob_is_docs as _glob_is_docs
from ._readiness import _is_umbrella as _is_umbrella
from ._readiness import _settled_umbrella_detail as _settled_umbrella_detail
from ._readiness import dep_status as dep_status
from ._readiness import inherited_deps as inherited_deps
from ._readiness import is_external as is_external
from ._readiness import plan_blocker as plan_blocker
from ._readiness import stale_package_globs as stale_package_globs
from ._readiness import unpickable as unpickable
from ._reserved import Caps as Caps
from ._reserved import caps as caps
from ._reserved import idle_reserved as idle_reserved
from ._reserved import open_slots as open_slots
from ._reserved import ordinary_free as ordinary_free
from ._reserved import reserved_in_force as reserved_in_force
from ._reserved import reserved_items as reserved_items
from ._resources import NO_TREE_TAG as NO_TREE_TAG
from ._resources import bug_items as bug_items
from ._resources import capacities as capacities
from ._resources import interrupted as interrupted
from ._resources import needs_tree as needs_tree
from ._resources import parse_resources as parse_resources
from ._resources import problem as problem
from ._resources import resource_shortfall as resource_shortfall
from ._waves import T as T
from ._waves import waves as waves
