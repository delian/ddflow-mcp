"""Copying git-ignored, machine-local files from the primary into a worktree."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import worktree as _W


def tracks_local_file(primary: Path | str, name: str) -> bool:
    """Is ``name`` tracked in ``primary``? The check ``copy_local_files_report`` skips on,
    exposed so a caller reporting WHAT was skipped does not re-implement it and drift."""
    return _W.git(Path(primary), "ls-files", "--error-unmatch", "--", name).ok


#: Why `copy_local_files_report` did not copy a file. The caller reporting the outcome
#: maps these to its own wording; re-deriving them from the filesystem drifted once
#: already (dedupe on 07bf509c).
SKIP_ABSENT = "absent"
SKIP_PRESENT = "present"
SKIP_OUTSIDE = "outside"
SKIP_TRACKED = "tracked"
SKIP_FAILED = "failed"


def copy_local_files_report(primary: Path, tree: Path, names: list[str]) -> dict[str, str | None]:
    """Copy ``names`` -- git-ignored, machine-local files -- from the primary checkout
    into ``tree``, reporting WHY each was or was not copied.

    A worktree is a git checkout, so an UNTRACKED file (a tool's local config, such as a
    .roborev.toml that must not be committed) is simply absent from it, and the tool
    falls back to whatever its global default is. ``name -> None`` means copied; anything
    else is one of the ``SKIP_*`` reasons, so a caller that has to explain the outcome
    does not have to re-derive it (and cannot disagree with what actually happened):

    * ``SKIP_ABSENT``   -- missing from the primary (nothing to copy).
    * ``SKIP_PRESENT``  -- already in the tree, never overwritten: the agent may have
      edited it. A DANGLING symlink counts as present -- ``exists()`` alone reported it
      absent, and copy2 then wrote through it to a target outside the tree (bug
      B6fb68b641e).
    * ``SKIP_OUTSIDE``  -- resolves outside the repository.
    * ``SKIP_TRACKED``  -- git tracks it: the checkout already brought it.
    * ``SKIP_FAILED``   -- the copy itself failed (an unreadable source, a full disk). A
      convenience file must not kill the claim that called this after its worktree
      exists, and the half-written copy is removed, since the next claim would otherwise
      keep it as "already in the tree".
    """
    out: dict[str, str | None] = {}
    root = Path(primary).resolve()
    for name in names:
        src = (root / name).resolve()
        dst = Path(tree) / name
        if not src.is_relative_to(root):
            out[name] = SKIP_OUTSIDE
            continue
        if not src.is_file():
            out[name] = SKIP_ABSENT
            continue
        if dst.exists() or dst.is_symlink():
            out[name] = SKIP_PRESENT
            continue
        if tracks_local_file(root, name):
            out[name] = SKIP_TRACKED
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            dst.unlink(missing_ok=True)
            out[name] = SKIP_FAILED
            continue
        out[name] = None
    return out


def copy_local_files(primary: Path, tree: Path, names: list[str]) -> list[str]:
    """The names actually copied, per `copy_local_files_report`."""
    return [n for n, why in copy_local_files_report(primary, tree, names).items() if why is None]
