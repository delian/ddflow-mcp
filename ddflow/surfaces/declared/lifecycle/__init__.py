"""The loop's commands, declared once, in order.

Holds the ``COMMANDS`` the single ``declared/lifecycle.py`` declared, split into
``_shared.py`` (imports + constants), ``navigate.py``, ``gates.py`` and ``outcome.py``
(one ``Command`` area each, so no module grows past the 30k file cap). ``COMMANDS`` here
keeps every command in its exact original order, and ``BY_TOOL`` is rebuilt from it, so
neither the CLI nor the MCP tool table changes byte for byte.

``COMMANDS`` and ``BY_TOOL`` are built here (no one submodule holds them), so they are
re-exported eager; the constants the single file held at module level live in
``_shared.py`` and are re-exported lazily through the shared ``ddflow/core/_reexport.py``
helper, exactly as the other split packages do.
"""

from __future__ import annotations

from ddflow.core import _reexport

from ...registry import by_tool
from ._shared import DEFAULT_CHECK_RECOVERY
from .gates import COMMANDS as _GATES
from .navigate import COMMANDS as _NAVIGATE
from .outcome import COMMANDS as _OUTCOME

COMMANDS: tuple = (*_NAVIGATE, *_GATES, *_OUTCOME)
BY_TOOL = by_tool(COMMANDS)

#: Every module-level name the single file held that now lives in ``_shared``; resolved
#: lazily by the shared helper so ``ddflow.surfaces.declared.lifecycle.<name>`` keeps
#: resolving exactly as it did before the split.
_SUBMODULE_OF = {
    "argparse": "_shared",
    "DEFAULT_NEXT_KIND": "_shared",
    "DEFAULT_WAIT_TIMEOUT_S": "_shared",
    "GATE_OUTCOMES": "_shared",
    "NOTHING": "_shared",
    "OK": "_shared",
    "GLOBS_HELP": "_shared",
    "_Globs": "_shared",
    "CliPolicy": "_shared",
    "Command": "_shared",
    "Param": "_shared",
    "MCP_WAIT_DEFAULT_S": "_shared",
    "MCP_WAIT_MAX_S": "_shared",
    "_api": "_shared",
    "_wait_timeout": "_shared",
    "AB": "_shared",
    "L": "_shared",
    "MUTATION_FLAG_EXEMPT": "_shared",
    "MUTATION_ORDER": "_shared",
    "MUTATION_PARAMS": "_shared",
    "mutation_of": "_shared",
    "_OVERRIDE": "_shared",
    "OVERRIDE_EXEMPT": "_shared",
    "_APPROVE_EPILOG": "_shared",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport

__all__ = ["BY_TOOL", "COMMANDS", "DEFAULT_CHECK_RECOVERY"]
