"""The loop's commands, declared once, in order.

Holds the ``COMMANDS`` the single ``declared/lifecycle.py`` declared, split into
``_shared.py`` (imports + constants), ``navigate.py``, ``gates.py`` and ``outcome.py``
(one ``Command`` area each, so no module grows past the 30k file cap). ``COMMANDS`` here
keeps every command in its exact original order, and ``BY_TOOL`` is rebuilt from it, so
neither the CLI nor the MCP tool table changes byte for byte.
"""

from __future__ import annotations

from ...registry import by_tool
from ._shared import DEFAULT_CHECK_RECOVERY
from .gates import COMMANDS as _GATES
from .navigate import COMMANDS as _NAVIGATE
from .outcome import COMMANDS as _OUTCOME

COMMANDS: tuple = (*_NAVIGATE, *_GATES, *_OUTCOME)
BY_TOOL = by_tool(COMMANDS)

__all__ = ["BY_TOOL", "COMMANDS", "DEFAULT_CHECK_RECOVERY"]
