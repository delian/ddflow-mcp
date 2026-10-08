"""How a result is written out: the one JSON emitter of the CLI's ``--json`` (D-unify,
B-uni-cmd-migrate; D-compat-json-views).

Every command used to print its own ``json.dumps(..., indent=2)``, twelve of them without
``default=str`` (so a Path or datetime in a body was a traceback there and a string
elsewhere). Every body a command in ``surfaces/commands``, ``cli.py`` or ``Ctx.out`` prints
now goes through one function (``tests/test_compat_json.py`` fails on a new ``json.dumps``
there), which is also the one place a rule about every body (the ``schema`` tag,
B-uni-compat-json) can live.

Standard library only: the surfaces and the registry may both import it.
"""

from __future__ import annotations

import json
from typing import Any, TextIO


def dumps(data: Any) -> str:
    """The text of a ``--json`` body: two-space indent, anything else JSON cannot hold as
    its ``str``."""
    return json.dumps(data, indent=2, default=str)


def emit_json(data: Any, *, file: TextIO | None = None) -> None:
    """Print ``data`` as a ``--json`` body (stdout unless ``file`` is given)."""
    print(dumps(data), file=file)
