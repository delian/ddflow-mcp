"""Leases — crash-recoverable ownership of a work item.

A lease is not a file and not a lock: it is an *event* with an expiry. That choice is
what makes crash recovery fall out for free. A lock file must be deleted by its holder,
so a killed holder leaves a lock nobody can safely remove. A lease simply stops being
renewed, and expiry is then a fact any observer can compute from the log.

**Expiry never steals the work.** ``lease.reclaim_policy`` defaults to ``report``: an
expired lease is surfaced with the worktree path and the state of the tree, and a human
(or an agent following the recovery procedure) decides; recovery is inspect, salvage,
then release.

This package is the former ``ddflow/services/leases.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.services.leases
import acquire`` and ``from ddflow.services import leases as L`` bind exactly what they
bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for. That keeps two invariants a one-file module had and a package must
keep — monkeypatch (``monkeypatch.setattr(L, name, ...)`` re-binds the name on THIS
module exactly as before) and the layering ratchet (``test_no_mutually_importing_pair``
would read an eager ``from .claim import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

import time as time  # re-exported: callers replace leases.time to fake the clock

from ddflow.core import _reexport

from ...core.admission import shared_globs as shared_globs  # re-exported

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # model
    "LeaseError": "model",
    "Recovery": "model",
    "Offer": "model",
    # common
    "_clone_field": "common",
    "_fold": "common",
    "glob_clash": "common",
    "glob_clashes": "common",
    "clash_report": "common",
    "unfinished_leases": "common",
    "holder_changed_paths": "common",
    "_clash_text": "common",
    "_age": "common",
    "_live_pairs": "common",
    # remote
    "_remote_take": "remote",
    "_remote_renew": "remote",
    "_remote_drop": "remote",
    # claim
    "_record_claimed_globs": "claim",
    "_renew_in_place": "claim",
    "acquire": "claim",
    "_holds_live": "claim",
    "_acquire_locked": "claim",
    "_prejudge": "claim",
    "_break_idle": "claim",
    "_alts": "claim",
    "_alternatives": "claim",
    # lifecycle
    "foreign": "lifecycle",
    "_transition": "lifecycle",
    "renew": "lifecycle",
    "plan_retarget": "lifecycle",
    "retarget": "lifecycle",
    "_released": "lifecycle",
    "release_claim": "lifecycle",
    "settle_remote": "lifecycle",
    "transfer": "lifecycle",
    "release": "lifecycle",
    "foreign_live_lease": "lifecycle",
    "foreign_guard": "lifecycle",
    "expire": "lifecycle",
    # recovery
    "scan": "recovery",
    "_tree_is_gone": "recovery",
    "open_session_model": "recovery",
    "_SHOWN_BLOCKERS": "recovery",
    "_landed_on_base": "recovery",
    "_note_landed": "recovery",
    "_measure": "recovery",
    "sweep": "recovery",
    # contests
    "resolve_command": "contests",
    "contest_row": "contests",
    "contested_items": "contests",
    "surface_contests": "contests",
    "contest_refusal": "contests",
    "_branches_of": "contests",
    "_same_clone": "contests",
    "_ours": "contests",
    "lost_contests": "contests",
    # module-level imports the original module re-exported, which callers read or
    # monkeypatch through this module (L.time, L.shared_globs, L.EventLog, L.clock,
    # L.localize, L.foreign, ...)
    "clock": "common",
    "localize": "common",
    "conflicts": "common",
    "is_shared": "common",
    "glob_conflict": "common",
    "line_key": "common",
    "fold": "common",
    "Config": "common",
    "State": "common",
    "Item": "common",
    "Lease": "common",
    "EventLog": "common",
    "GL": "common",
    "DONE": "claim",
    "REVIEW": "recovery",
    "capacities": "claim",
    "plan_blocker": "claim",
    "resource_shortfall": "claim",
    "O": "contests",
    "CR": "remote",
    "LB": "claim",
    "WG": "lifecycle",
    "quiet_renewal": "lifecycle",
    "renew_window": "lifecycle",
}

# Importing it registers the log's append hook that keeps `.ddflow/local/lease-snapshot.json`
# current (LK-hook-snapshot); it imports nothing from this package, so it is no cycle.
from . import snapshot as snapshot  # noqa: E402

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
