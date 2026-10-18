"""Commands declared once (`registry.Command`): the parser and the MCP tool entry of each
are generated from the declaration (D-unify 4, B-uni-cmd-migrate).

One module per family. The tool modules (`surfaces/tools/`) take a family's entries from
here and the parser modules (`surfaces/parsers/`) register its commands, so neither holds a
second copy of a name, a help text or a type.

The declarations' `call` and `payload` use the helpers in `surfaces/tools/_common.py`,
and the tool modules import the declarations: importing the tool package first, as below,
makes the cycle resolve the same way whichever side a process enters from.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import ModuleType

_HAS_COMMANDS = re.compile(r"^COMMANDS\b", re.MULTILINE)
_HAS_TOOL = re.compile(r'\btool\s*=\s*"ddflow_')


def family_modules(*, tools: bool = False) -> tuple[ModuleType, ...]:
    """Every module of this package that declares ``COMMANDS``, by file name.

    Registration by convention: a new command family is a new file here with a module-level
    ``COMMANDS`` tuple, and no registry file names it. The source is read first so a helper
    module (``*_cli``, ``mutation_params``) that declares none is never imported by the scan.
    ``tools=True`` also skips a family with no ``tool="ddflow_..."`` (a CLI-only one), so the
    MCP tool table does not import what only the command line needs.
    """
    found = []
    for path in sorted(Path(__file__).parent.glob("[a-z]*.py")):
        text = path.read_text("utf-8")
        if _HAS_COMMANDS.search(text) and (not tools or _HAS_TOOL.search(text)):
            found.append(importlib.import_module(f"{__name__}.{path.stem}"))
    return tuple(found)


def all_commands(*, tools: bool = False) -> tuple:
    """The commands of every family, in file-name order (``tools``: see `family_modules`)."""
    return tuple(c for m in family_modules(tools=tools) for c in m.COMMANDS)


# Last: the tool package imports `all_commands` from here, so it is defined above.
from .. import tools as _tools  # noqa: E402,F401 -- see the docstring
