"""`ddflow upgrade --apply`: do what `upgrade --plan` lists, with a backup first.

This package is the former ``ddflow/services/upgrade_apply.py``, split into submodules
that stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.services import
upgrade_apply as UA`` binds exactly what it bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. That keeps two invariants a one-file module had and a package must keep —
monkeypatch (``monkeypatch.setattr(UA, name, ...)`` re-binds the name on THIS module
exactly as before) and the layering ratchet (``test_no_mutually_importing_pair`` would read
an eager ``from ._apply import ...`` here as a latent parent<->child cycle). The lone
group that is NOT lazy is the sibling re-export block: ``UP`` (``services.upgrade_plan``)
and the ``services.backups`` names were imported by the old module for re-export, and no
submodule holds them.
"""

from __future__ import annotations

from ddflow.core import _reexport

from .. import upgrade_plan as UP  # noqa: F401 -- the ``UA.UP`` alias the old module bound
from ..backups import (  # noqa: F401 -- the backups' home
    BACKUPS,
    MANIFEST,
    SnapshotRefused,
    backup_name,
    make_backup,
    make_snapshot,
    prune,
)

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # _constants
    "ACKNOWLEDGED": "_constants",
    "APPLIED": "_constants",
    "BACKUP_MODES": "_constants",
    "CONFIG_POLICIES": "_constants",
    "FAILED": "_constants",
    "REFUSED": "_constants",
    "SKIPPED": "_constants",
    "UNAVAILABLE": "_constants",
    "_VERSIONED": "_constants",
    # _appliers
    "_abs": "_appliers",
    "_apply_config": "_appliers",
    "_apply_hook": "_appliers",
    "_apply_mcp": "_appliers",
    "_config_files": "_appliers",
    "_remove_key": "_appliers",
    "_run": "_appliers",
    "_short": "_appliers",
    "_value": "_appliers",
    "_writes_config": "_appliers",
    "parse_categories": "_appliers",
    "touched": "_appliers",
    # _apply
    "_cats_of": "_apply",
    "_check_modes": "_apply",
    "_close": "_apply",
    "_confirmed": "_apply",
    "_earlier_cats": "_apply",
    "_execute": "_apply",
    "_finish": "_apply",
    "_ident": "_apply",
    "_ids": "_apply",
    "_in_order": "_apply",
    "_LAST": "_apply",
    "_lags": "_apply",
    "_late_saver": "_apply",
    "_needs_confirmation": "_apply",
    "_not_saved": "_apply",
    "_note": "_apply",
    "_owner": "_apply",
    "_rec": "_apply",
    "_record": "_apply",
    "_reasons": "_apply",
    "_refusal": "_apply",
    "_run_instructions": "_apply",
    "_run_migrations": "_apply",
    "_run_repairs": "_apply",
    "_save": "_apply",
    "_select": "_apply",
    "_settled_steps": "_apply",
    "_start": "_apply",
    "_step": "_apply",
    "_stepper": "_apply",
    "_summarise": "_apply",
    "_unresolved": "_apply",
    "_with_earlier": "_apply",
    "Watch": "_apply",
    "apply": "_apply",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
