"""Install and uninstall the shell completion activation line.

Installation is offered, never silent (`ddflow completion` offers at onboarding and asks the
operator; the explicit `install` command requires `--yes`). It APPENDS one marker-guarded
block to the shell's startup file; `uninstall` removes ONLY that block -- the files and
lines it did not write stay. The block sources `ddflow completion <shell>`, so the
activation script itself stays the single source of truth, and the block differs per shell
(fish has no process substitution, so its line pipes the script into ``source``).
"""

from __future__ import annotations

import os
from pathlib import Path

_BEGIN = "# >>> ddflow completion >>>"
_END = "# <<< ddflow completion <<<"


def target_file(shell: str, home: str | Path) -> Path:
    """The startup file a shell reads interactively (what install would edit).

    ``home`` is the fallback; where the shell itself looks first wins: zsh honours
    ``ZDOTDIR``, fish honours ``XDG_CONFIG_HOME`` (both defaulting back to ``home`` when
    unset), so install edits the file the shell will actually read.
    """
    root = Path(home).expanduser()
    if shell == "fish":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg).expanduser() if xdg else root / ".config"
        return base / "fish" / "config.fish"
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR")
        base = Path(zdotdir).expanduser() if zdotdir else root
        return base / ".zshrc"
    if shell == "bash":
        return root / ".bashrc"
    raise ValueError(f"not a completion shell: {shell!r}")


def _activate_line(shell: str) -> str:
    if shell == "fish":
        return f"ddflow completion {shell} | source"
    return f"source <(ddflow completion {shell})"


def install_text(shell: str, current: str) -> str:
    """``current`` plus one activation block, appended only when the activation line is
    absent (idempotent). Append-only: trailing newlines stay exactly as they were."""
    line = _activate_line(shell)
    if any(ln.strip() == line for ln in current.splitlines()):
        return current
    block = f"{_BEGIN}\n{line}\n{_END}\n"
    if not current or current.endswith("\n"):
        return current + block
    return current + "\n" + block


def uninstall_text(shell: str, current: str) -> str:
    """``current`` with the activation block it may carry removed. A begin marker whose
    end follows drops that begin..end span; a dangling begin (no end before EOF) drops only
    the marker line -- never the rest of the file."""
    del shell
    lines = current.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == _BEGIN:
            j = i + 1
            while j < len(lines) and lines[j].strip() != _END and lines[j].strip() != _BEGIN:
                j += 1
            if j < len(lines) and lines[j].strip() == _END:  # a CLOSED block: skip begin..end
                i = j + 1
                continue
            i += 1  # a dangling begin (EOF or another begin first): drop only this marker
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)
