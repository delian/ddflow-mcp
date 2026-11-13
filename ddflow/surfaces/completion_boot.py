"""Answer a shell completion request before anything else is imported.

The shell's registration (`ddflow completion <shell>`) runs the ``ddflow`` command with
``_ARGCOMPLETE`` set; argcomplete's contract is that the program then answers on fd 8 and
exits instead of running. `ddflow/surfaces/__init__.py` calls `answer_if_asked`, which runs before
`ddflow.surfaces.cli` is imported, so it fires for the setuptools console script and
``python -m ddflow`` alike, and costs one environment lookup on every other run.

Importing this module imports only the standard library.
"""

from __future__ import annotations

import os
import sys
from importlib import import_module

#: The environment variable argcomplete's registration sets on the program it asks.
REQUEST_ENV = "_ARGCOMPLETE"
#: The program names that answer (a console script, or `python -m ddflow`, whose argv[0] is `-m` while the package imports).
_NAMES = ("ddflow", "__main__.py", "-m")


def asked(
    environ: os._Environ[str] | dict[str, str] | None = None, argv0: str | None = None
) -> bool:
    """Is this process a completion request for the ``ddflow`` command?"""
    env = os.environ if environ is None else environ
    if REQUEST_ENV not in env:
        return False
    name = os.path.basename(sys.argv[0] if argv0 is None else argv0)
    return name in _NAMES


def answer_if_asked() -> None:
    """Answer and exit when this is a completion request; return at once otherwise."""
    if not asked():
        return
    engine = import_module("ddflow.api.completion_engine")
    engine.answer(import_module("ddflow.surfaces.cli").build_parser())
    # argcomplete exits the process when it answers; reaching here means it is not installed
