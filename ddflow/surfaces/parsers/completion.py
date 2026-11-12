"""`ddflow completion <bash|zsh|fish>`: the shell activation script for ddflow completion."""

from __future__ import annotations

import argparse

from ...services.completion import scripts
from ..context import Ctx


def _cmd_completion(a: argparse.Namespace, c: Ctx) -> int:
    del c
    print(scripts.for_shell(a.shell), end="")
    return 0


def register(s: argparse._SubParsersAction) -> None:
    """Add `completion` to `s`, the root `ddflow` subparsers."""
    c = s.add_parser("completion", help="print the shell activation script for ddflow completion")
    c.add_argument("shell", choices=("bash", "zsh", "fish"), help="the shell to activate")
    c.set_defaults(fn=_cmd_completion)
