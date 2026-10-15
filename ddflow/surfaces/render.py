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

import io
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, TextIO

from .registry import tag_body

#: The command a CLI process is running (``gate_record``), set once by `cli.main`; the schema
#: tag of an object body is made from it. Empty outside a command, and bodies are not tagged.
_COMMAND: ContextVar[str] = ContextVar("ddflow_command", default="")


def id_cell(ident: str, width: int = 22, *, cut: bool = False) -> str:
    """An id padded to `width` columns, never cut: a shortened id is one `show` rejects.

    `cut=True` is only for a log event's id (a long hash no command takes), shown as a prefix."""
    return f"{ident:<{width}.{width}s}" if cut else f"{ident:<{width}s}"


def set_command(name: str) -> Token[str]:
    """Name the command whose bodies `emit_json` now tags. The token puts the previous name
    back (`reset_command`): a run that ends must not tag what is printed after it."""
    return _COMMAND.set(name)


def reset_command(token: Token[str]) -> None:
    """Undo the `set_command` that returned ``token``."""
    _COMMAND.reset(token)


def dumps(data: Any) -> str:
    """The text of a ``--json`` body: two-space indent, anything else JSON cannot hold as
    its ``str``."""
    return json.dumps(data, indent=2, default=str)


#: How many bodies this process has printed to stdout (`bodies_emitted`).
_EMITTED = [0]


def bodies_emitted() -> int:
    """How many ``--json`` bodies have gone to stdout so far: a run that ends with the same
    count it began with printed none."""
    return _EMITTED[0]


def emit_json(data: Any, *, file: TextIO | None = None) -> None:
    """Print ``data`` as a ``--json`` body (stdout unless ``file`` is given)."""
    if file is None:
        _EMITTED[0] += 1
    print(dumps(tag_body(data, _COMMAND.get())), file=file)


class _Tee(io.TextIOBase):
    """stderr that also remembers what was written to it."""

    def __init__(self, inner: TextIO) -> None:
        self.inner, self.text = inner, ""

    def write(self, s: str) -> int:
        self.text += s
        return self.inner.write(s)

    def flush(self) -> None:
        self.inner.flush()


@contextmanager
def remembered_stderr() -> Iterator[_Tee]:
    """Run with stderr remembered, so a refusal that said its reason only there can say it
    in the ``--json`` body too (bug Bac9d773a44)."""
    real = sys.stderr
    tee = _Tee(real)
    sys.stderr = tee
    try:
        yield tee
    finally:
        sys.stderr = real


def refusal_body(code: int, said: str, outcome: str) -> dict[str, Any]:
    """The body of a ``--json`` run that exited ``code`` without printing one: the reason it
    said on stderr, led by `refusal` as every refusal body is."""
    reason = said.strip() or f"exit {code}"
    return {"refusal": {"reason": reason, "outcome": outcome, "exit": code}}
