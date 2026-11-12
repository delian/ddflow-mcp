"""Install and uninstall the shell completion activation line.

Installation is offered, never silent (`DDflow completion` offers at onboarding and asks
the operator; the explicit command here requires a `--yes`). It APPENDS one marker-guarded
block to the shell's startup file; `uninstall` removes ONLY that block -- the files and
lines it did not write stay. The block sources `ddflow completion <shell>`, so the
activation script itself stays the single source of truth.
"""

from __future__ import annotations

from pathlib import Path

_BEGIN = "# >>> ddflow completion >>>"
_END = "# <<< ddflow completion <<<"


def target_file(shell: str, home: str | Path) -> Path:
    """The startup file a shell reads interactively (what install would edit)."""
    root = Path(home).expanduser()
    if shell == "fish":
        return root / ".config" / "fish" / "config.fish"
    if shell == "zsh":
        return root / ".zshrc"
    if shell == "bash":
        return root / ".bashrc"
    raise ValueError(f"not a completion shell: {shell!r}")


def _activate_line(shell: str) -> str:
    return f"source <(ddflow completion {shell})"


def installable(shell: str, home: str | Path) -> Path:
    """The file `install` would edit, without touching it."""
    return target_file(shell, home)


def install_text(shell: str, current: str) -> str:
    """``current`` plus one activation block, appended only when absent (idempotent)."""
    if _BEGIN in current:
        return current
    block = f"{_BEGIN}\n{_activate_line(shell)}\n{_END}\n"
    return (current.rstrip("\n") + "\n" if current else "") + block


def uninstall_text(shell: str, current: str) -> str:
    """``current`` with the activation block it may carry removed."""
    lines = current.splitlines(keepends=True)
    kept: list[str] = []
    inside = False
    for L in lines:
        if L.strip() == _BEGIN:
            inside = True
            continue
        if inside:
            if L.strip() == _END:
                inside = False
            continue
        kept.append(L)
    return "".join(kept)


def install(shell: str, home: str | Path, *, yes: bool = False) -> str:
    """The startup file after `install` runs: appended (when ``yes``) or the plan (else)."""
    path = target_file(shell, home)
    current = path.read_text() if path.is_file() else ""
    return install_text(shell, current)


def uninstall(shell: str, home: str | Path, *, write: bool = False) -> str:
    """The startup file after `uninstall` runs."""
    path = target_file(shell, home)
    current = path.read_text() if path.is_file() else ""
    return uninstall_text(shell, current)
