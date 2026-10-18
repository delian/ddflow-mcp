"""A `ddflow` that runs THIS worktree's code (DX-worktree-bin-on-path).

The `ddflow` on PATH resolves to the primary checkout's venv, so a manual check inside a
claimed worktree of ddflow itself exercised main's code, not the branch's. A claim of a
tree holding a ddflow source package writes a shim at `<tree>/.ddflow/local/bin/ddflow`
(git-ignored with the rest of `local/`) that pins `PYTHONPATH` to the tree; running it, or
putting its directory first on PATH, makes the branch's code the one that runs. The
installed command is untouched.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from ..infra import fsio
from ..infra import paths as PA

SHIM_REL = Path(".ddflow") / "local" / "bin" / "ddflow"


def is_source_tree(tree: Path) -> bool:
    return (Path(tree) / "ddflow" / "__init__.py").is_file()


def write_shim(tree: Path) -> Path | None:
    """Write (or refresh) the tree's shim; None when `tree` holds no ddflow source."""
    tree = Path(tree).resolve()
    if not is_source_tree(tree):
        return None
    shim = tree / SHIM_REL
    body = (
        "#!/bin/sh\n# runs the ddflow code of the tree this file lives in\n"
        f'PYTHONPATH={shlex.quote(str(tree))}"${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'exec {shlex.quote(sys.executable)} -m ddflow "$@"\n'
    )
    try:
        if not shim.is_file() or shim.read_text() != body:
            fsio.atomic_write(shim, body, mode=0o755, fsync=False)
    except OSError:
        return None
    return shim


def advice(tree: Path) -> str:
    """The claim line naming the shim, or "" when the tree has none to offer."""
    shim = write_shim(tree)
    if shim is None:
        return ""
    return (
        f"ddflow from this tree's code: {shim}  "
        f"(or: export PATH={shlex.quote(str(shim.parent))}:$PATH). "
        "The `ddflow` on PATH runs the primary's code."
    )


def stale_notes(cwd: Path) -> list[str]:
    """A doctor warning when `cwd` is in a ddflow source tree other than the running one."""
    top = _top(Path(cwd))
    if top is None or not is_source_tree(top):
        return []
    running = PA.package_parent()
    if running == top:
        return []
    shim = top / SHIM_REL
    return [
        f"this ddflow runs the code in {running}, not the tree you are in ({top}): a manual "
        f"check here tests the wrong code. Use {shim if shim.is_file() else 'a claim-made shim'}"
        f" or `export PYTHONPATH={shlex.quote(str(top))}`."
    ]


def _top(cwd: Path) -> Path | None:
    for p in (cwd.resolve(), *cwd.resolve().parents):
        if (p / ".git").exists():
            return p
    return None


def with_advice(first: str, wt) -> str:
    """`first` (the claim's advice lines so far) plus the shim line for worktree `wt`."""
    return "\n  ".join(filter(None, (first, advice(Path(wt.path)) if wt else "")))
