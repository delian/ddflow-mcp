"""Install and uninstall the shell completion activation line.

Installation is offered, never silent (`ddflow completion` offers at onboarding and asks the
operator; the explicit `install` command requires `--yes`). It APPENDS one marker-guarded
block to the shell's startup file; `uninstall` removes ONLY that block -- the files and
lines it did not write stay. The block sources `ddflow completion <shell>`, so the
activation script itself stays the single source of truth, and the block differs per shell
(fish has no process substitution, so its line pipes the script into ``source``).
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
    if shell == "fish":
        return f"ddflow completion {shell} | source"
    return f"source <(ddflow completion {shell})"


def install_text(shell: str, current: str) -> str:
    """``current`` plus one activation block, appended only when absent (idempotent)."""
    if _BEGIN in current:
        return current
    block = f"{_BEGIN}\n{_activate_line(shell)}\n{_END}\n"
    return (current.rstrip("\n") + "\n" if current else "") + block


def uninstall_text(shell: str, current: str) -> str:
    """``current`` with the activation block it may carry removed. A closed block drops
    its begin/source/end lines; a dangling begin marker (no end) drops only that marker,
    never the rest of the file."""
    del shell
    lines = current.splitlines(keepends=True)
    has_end = any(L.strip() == _END for L in lines)
    kept: list[str] = []
    inside = False
    for L in lines:
        if L.strip() == _BEGIN:
            inside = has_end  # only a CLOSED block eats following lines
            continue
        if inside:
            if L.strip() == _END:
                inside = False
            continue
        kept.append(L)
    return "".join(kept)
