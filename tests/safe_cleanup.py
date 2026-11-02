"""Test cleanup that cannot delete what the test does not own (B-e5e7dc7e27).

A deletion target derived from a value under test ("" -> ``Path('.')``) once made a failing
assertion remove the repository. Cleanup goes through ``remove_owned``: it refuses before any
deletion unless the target is absolute, non-empty and strictly inside a directory the test owns.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def remove_owned(target: str | Path | None, root: str | Path) -> None:
    """Remove ``target`` (a directory) only when it is strictly inside ``root``; else raise."""
    if target is None or str(target).strip() == "":
        raise ValueError(f"refusing to delete: empty target {target!r}")
    path = Path(target)
    if not path.is_absolute():
        raise ValueError(f"refusing to delete: relative target {str(target)!r}")
    if (
        ".." in path.parts
        or str(root).strip() == ""
        or not Path(root).is_absolute()
        or Path(root).is_symlink()
    ):
        raise ValueError(
            f"refusing to delete {str(target)!r}: '..' in it, or root {str(root)!r} not absolute"
        )
    base = Path(root).resolve()
    resolved = path.parent.resolve() / path.name  # the link itself, not where it points
    if resolved == base or base not in resolved.parents:
        raise ValueError(f"refusing to delete {str(target)!r}: not strictly inside {str(base)!r}")
    if path.is_symlink():  # the link is ours to drop; never follow it to its target
        path.unlink()
    else:
        shutil.rmtree(path, ignore_errors=True)
