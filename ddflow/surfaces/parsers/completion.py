"""`ddflow completion`: print the activation script, or install/uninstall it."""

from __future__ import annotations

import argparse
import os

from ...api import completion_scripts
from ...api.completion import install as _install
from ...adapters.fsio import atomic_write
from ..context import Ctx


def _cmd_completion(a: argparse.Namespace, c: Ctx) -> int:
    del c
    if a.cmd == "install":
        return _do_install(a)
    if a.cmd == "uninstall":
        return _do_uninstall(a)
    print(completion_scripts.for_shell(a.cmd), end="")
    return 0


def _home() -> str:
    return os.path.expanduser("~")


def _do_install(a: argparse.Namespace) -> int:
    sh = a.shell or "bash"
    path = _install.target_file(sh, _home())
    current = path.read_text() if path.is_file() else ""
    text = _install.install_text(sh, current)
    if not a.yes:
        print(f"would append one guarded block to {path}:")
        print(text[len(current) :] or "(already installed)")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if text == current:
        print(f"already installed in {path} (no change)")
        return 0
    atomic_write(path, text)
    print(f"installed ddflow completion into {path}")
    return 0


def _do_uninstall(a: argparse.Namespace) -> int:
    sh = a.shell or "bash"
    path = _install.target_file(sh, _home())
    if not path.is_file():
        print(f"no completion block in {path} (nothing to remove)")
        return 0
    current = path.read_text()
    text = _install.uninstall_text(sh, current)
    if text == current:
        print(f"no completion block in {path} (nothing to remove)")
        return 0
    if not a.yes:
        print(f"would uninstall the ddflow completion block from {path}")
        return 0
    atomic_write(path, text)
    print(f"uninstalled ddflow completion from {path}")
    return 0


def register(s: argparse._SubParsersAction) -> None:
    """Add `completion` to `s`, the root `ddflow` subparsers."""
    c = s.add_parser("completion", help="activate, install or uninstall ddflow completion")
    c.add_argument(
        "cmd",
        choices=("bash", "zsh", "fish", "install", "uninstall"),
        help="the shell to activate, or install/uninstall the activation block",
    )
    c.add_argument(
        "shell",
        nargs="?",
        choices=("bash", "zsh", "fish"),
        help="for install/uninstall: the shell to edit (default bash)",
    )
    c.add_argument("--yes", action="store_true", help="consent to write (never written silently)")
    c.set_defaults(fn=_cmd_completion)
