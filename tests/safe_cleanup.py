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
    base = Path(root).resolve()
    resolved = path.resolve()
    if resolved == base or base not in resolved.parents:
        raise ValueError(f"refusing to delete {str(target)!r}: not strictly inside {str(base)!r}")
    shutil.rmtree(resolved, ignore_errors=True)
