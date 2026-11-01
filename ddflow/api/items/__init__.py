"""Creating and changing queue items.

This package is the former ``ddflow/api/items.py`` (48k chars), split into submodules
that stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.api.items import
update`` and ``from ddflow.api import items as I`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. ``_load`` and ``DEFAULT_PRIORITY`` stay eager, because they are the names a
test monkeypatches on THIS module (``monkeypatch.setattr(items, "_load", ...)``) and the
submodules reach ``_load`` through it, exactly as the single module's functions did.
"""

from __future__ import annotations

from ddflow.core import _reexport

from ...core.defaults import DEFAULT_PRIORITY as DEFAULT_PRIORITY
from .._base import _load as _load

#: Public (and patch-covered) name -> the submodule that defines it.
_SUBMODULE_OF = {
    "ItemEdit": "_edit",
    "MIN_SPLIT_PARTS": "_split",
    "phase_add": "_add",
    "phase_close": "_add",
    "plan_edit": "_edit",
    "resolve": "_resolve",
    "split": "_split",
    "task_add": "_add",
    "update": "_edit",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
