"""`ddflow completion`: print the activation script, or install/uninstall it."""

from __future__ import annotations

import argparse

from ...adapters.env import DEFAULT as _ENV
from ...adapters.fsio import replace_text
from ...api import completion_scripts
from ...api.completion import install as _install
from ..context import Ctx


def _say(c: Ctx | None, message: str, **data: object) -> None:
    """Print ``message``; under ``--json`` the body is ``data`` plus the message instead."""
    if c is not None and c.json:
        c.out(message, {"message": message, **data})
    else:
        print(message)


def _cmd_completion(a: argparse.Namespace, c: Ctx) -> int:
    if a.cmd == "install":
        return _do_install(a, c)
    if a.cmd == "uninstall":
        return _do_uninstall(a, c)
    script = completion_scripts.for_shell(a.cmd)
    if c.json:
        c.out("", {"shell": a.cmd, "script": script})
    else:
        print(script, end="")
    return 0


def _home() -> str:
    return _ENV.get("HOME") or "~"


def _do_install(a: argparse.Namespace, c: Ctx | None = None) -> int:
    sh = a.shell or "bash"
    path = _install.target_file(sh, _home())
    current = path.read_text() if path.is_file() else ""
    text = _install.install_text(sh, current)
    if not a.yes:
        _say(
            c,
            f"would append one guarded block to {path}:\n"
            + (text[len(current) :] or "(already installed)"),
            path=str(path),
            written=False,
        )
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if text == current:
        _say(c, f"already installed in {path} (no change)", path=str(path), written=False)
        return 0
    replace_text(path, text)
    _say(c, f"installed ddflow completion into {path}", path=str(path), written=True)
    return 0


def _do_uninstall(a: argparse.Namespace, c: Ctx | None = None) -> int:
    sh = a.shell or "bash"
    path = _install.target_file(sh, _home())
    if not path.is_file():
        _say(c, f"no completion block in {path} (nothing to remove)", path=str(path), written=False)
        return 0
    current = path.read_text()
    text = _install.uninstall_text(sh, current)
    if text == current:
        _say(c, f"no completion block in {path} (nothing to remove)", path=str(path), written=False)
        return 0
    if not a.yes:
        _say(c, f"would uninstall the ddflow completion block from {path}", path=str(path), written=False)
        return 0
    replace_text(path, text)
    _say(c, f"uninstalled ddflow completion from {path}", path=str(path), written=True)
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
