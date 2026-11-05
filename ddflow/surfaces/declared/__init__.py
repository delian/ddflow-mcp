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

_HAS_COMMANDS = re.compile(r"^COMMANDS\s*[:=]", re.MULTILINE)
#: A non-empty ``tool=`` of any spelling: the scan only decides what is IMPORTED.
_HAS_TOOL = re.compile(r"""\btool\s*=\s*(?!""|'')[^\s)]""")


def family_modules(*, tools: bool = False) -> tuple[ModuleType, ...]:
    """Every module of this package that declares ``COMMANDS``, by file name.

    Registration by convention: a new command family is a new file here with a module-level
    ``COMMANDS`` tuple, and no registry file names it. A family may also be a package whose
    ``__init__.py`` declares ``COMMANDS`` and re-exports it (the file split in two or more
    area modules to stay under the file-size cap): its source is read the same way, so a
    helper module (``*_cli``, ``mutation_params``) that declares none is never imported by
    the scan. ``tools=True`` also skips a family with no ``tool="ddflow_..."`` (a CLI-only
    one), so the MCP tool table does not import what only the command line needs.
    """
    found = []
    names = sorted(
        {p.stem for p in Path(__file__).parent.glob("[a-z]*.py")}
        | {p.parent.name for p in Path(__file__).parent.glob("[a-z]*/__init__.py")}
    )
    for name in names:
        file = Path(__file__).parent / f"{name}.py"
        if file.is_file():
            text = file.read_text("utf-8")
        else:  # a package family: COMMANDS in its __init__, `tool=` in its area modules
            text = "\n".join(
                p.read_text("utf-8") for p in sorted((Path(__file__).parent / name).glob("*.py"))
            )
        if _HAS_COMMANDS.search(text) and (not tools or _HAS_TOOL.search(text)):
            module = importlib.import_module(f"{__name__}.{name}")
            if isinstance(
                getattr(module, "COMMANDS", None), (tuple, list)
            ):  # the text match can be fooled
                found.append(module)
    return tuple(found)


def all_commands(*, tools: bool = False) -> tuple:
    """The commands of every family, in file-name order (``tools``: see `family_modules`)."""
    return tuple(c for m in family_modules(tools=tools) for c in m.COMMANDS)


# Last: the tool package imports `all_commands` from here, so it is defined above.
from .. import tools as _tools  # noqa: E402,F401 -- see the docstring
