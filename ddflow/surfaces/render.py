"""How a result is written out: the one JSON emitter of the CLI's ``--json`` (D-unify,
B-uni-cmd-migrate; D-compat-json-views).

Every command used to print its own ``json.dumps(..., indent=2)``, twelve of them without
``default=str`` (so a Path or datetime in a body was a traceback there and a string
elsewhere). Every body a command in ``surfaces/commands``, ``cli.py`` or ``Ctx.out`` prints
now goes through one function (``tests/test_compat_json.py`` fails on a new ``json.dumps``
there), which is also the one place a rule about every body (the ``schema`` tag,
B-uni-compat-json) can live.

Standard library only (and the registry): the surfaces may import it.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, TextIO

from .registry import tag_body

#: The command a CLI process is running (``gate_record``), set once by `cli.main`; the schema
#: tag of an object body is made from it. Empty outside a command, and bodies are not tagged.
_COMMAND: ContextVar[str] = ContextVar("ddflow_command", default="")


def set_command(name: str) -> None:
    """Name the command whose bodies `emit_json` now tags."""
    _COMMAND.set(name)


def dumps(data: Any) -> str:
    """The text of a ``--json`` body: two-space indent, anything else JSON cannot hold as
    its ``str``."""
    return json.dumps(data, indent=2, default=str)


def emit_json(data: Any, *, file: TextIO | None = None) -> None:
    """Print ``data`` as a ``--json`` body (stdout unless ``file`` is given)."""
    print(dumps(tag_body(data, _COMMAND.get())), file=file)
