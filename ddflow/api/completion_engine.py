"""The shell-completion engine: argcomplete over the real argparse parser.

`answer(parser)` gives every argument whose value kind is declared (`Param.complete`) or
inferred (`services.completion.kinds.infer`) a completer that asks `services.completion.values`
-- record ids from the derived index, gate names, states, agent names, paths -- and then
hands the parser to ``argcomplete.autocomplete``, which answers the shell on the argcomplete
protocol (environment variables, answer on fd 8) and exits the process. Nothing here names
a command, an option or a choice: they are the parser's own.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from importlib import import_module
from typing import Any

from ..services import completion_ids as CI
from ..services.completion import kinds as K
from ..services.completion import values as V


def value_completer(kind: str) -> Callable[..., Iterable[str]]:
    """The argcomplete completer for an argument of value ``kind`` (never raises)."""

    def complete(prefix: str = "", parsed_args: Any = None, **_: Any) -> list[str]:
        root = getattr(parsed_args, "repo", None) or None
        try:
            return V.complete(kind, prefix, index=CI.find_index(root), root=root)
        except Exception:  # a TAB that prints a traceback is worse than one that offers nothing
            return []

    return complete


def attach(parser: argparse.ArgumentParser, record_kind: str | None = None) -> None:
    """Set ``completer`` on every value-taking action of ``parser`` and its subcommands
    whose kind is a value kind (not free text, not a closed ``choices`` set)."""
    id_words = K.id_kinds()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                attach(child, name if record_kind is None and name in id_words else record_kind)
            continue
        if action.nargs == 0 or getattr(action, "completer", None) is not None:
            continue
        metavar = action.metavar if isinstance(action.metavar, str) else None
        kind = getattr(action, "complete", "") or K.infer(
            action.dest or "",
            choices=action.choices,
            metavar=metavar,
            positional=not action.option_strings,
            record_kind=record_kind,
        )
        if kind not in K.FIXED[:2]:  # "none" and "enum" (argparse's own choices) need none
            action.completer = value_completer(kind)  # type: ignore[attr-defined]


def answer(parser: argparse.ArgumentParser) -> None:
    """Answer a completion request and exit the process; return only when argcomplete is not
    installed (the caller then ends the request without running the CLI)."""
    try:
        argcomplete = import_module("argcomplete")
    except ImportError:
        return
    attach(parser)
    argcomplete.autocomplete(parser)
