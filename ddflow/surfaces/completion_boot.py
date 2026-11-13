"""Answer a shell completion request before anything else is imported.

The shell's registration (`ddflow completion <shell>`) runs the ``ddflow`` command with
``_ARGCOMPLETE`` set; argcomplete's contract is that the program then answers on fd 8 and
exits instead of running. `ddflow/surfaces/__init__.py` calls `answer_if_asked`, which runs before
`ddflow.surfaces.cli` is imported, so it fires for the setuptools console script and
``python -m ddflow`` alike, and costs one environment lookup on every other run.

Importing this module imports only the standard library and `adapters.env` (stdlib only).
"""

from __future__ import annotations

import sys
from importlib import import_module

from ..adapters import env as E

#: The environment variable argcomplete's registration sets on the program it asks.
REQUEST_ENV = "_ARGCOMPLETE"


def _module_run(orig_argv: list[str]) -> bool:
    """Was this process started as ``python -m ddflow`` (or a submodule of it)? ``sys.argv[0]`` is only ``-m`` (or the path of ``__main__.py``), so the module name
    comes from the interpreter's original arguments."""
    args = iter(orig_argv[1:])
    for arg in args:
        if arg == "-m":  # the interpreter's own module switch, before any script word
            module = next(args, "")
            return module == "ddflow" or module.startswith("ddflow.")
        if arg == "-c" or arg == "--":
            return False  # an inline program, or the end of the options: not a module run
        if arg in ("-W", "-X"):
            next(args, None)  # an interpreter option that takes a value
        elif not arg.startswith("-"):
            return False  # a script path: later words are the script's, not the interpreter's
    return False


def asked(argv0: str | None = None, orig_argv: list[str] | None = None) -> bool:
    """Is this process a completion request for the ``ddflow`` command?"""
    if E.SystemEnv().get(REQUEST_ENV) is None:
        return False
    name = (sys.argv[0] if argv0 is None else argv0).rsplit("/", 1)[-1]
    if name in ("-m", "__main__.py"):  # `-m` while a package imports, the path once __main__ runs
        return _module_run(sys.orig_argv if orig_argv is None else orig_argv)
    return name == "ddflow"


def answer_if_asked() -> None:
    """Answer and exit when this is a completion request; return at once otherwise."""
    if not asked():
        return
    engine = import_module("ddflow.api.completion_engine")
    engine.answer(import_module("ddflow.surfaces.cli").build_parser())
    # argcomplete exits the process when it answers; reaching here means it is not
    # installed. The shell asked a question: never run the CLI with its words, say nothing.
    sys.exit(1)
