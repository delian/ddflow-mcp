"""Where the MCP interface snapshot is written (SU-init-fast): the one place that touches the file.

The surface that builds the snapshot (`surfaces.ifacesnap`) reaches the filesystem only through
the api. The directory is the project's machine-local one and ignores itself, so the snapshot
is never committed; the write is atomic, so the shim never reads half a file.
"""

from __future__ import annotations

from pathlib import Path

from ..infra import fsio
from ..mcpshim import snapshot as shim_snapshot


def write(repo: Path | str, text: str) -> Path:
    """Replace the snapshot of `repo` with `text`; returns the path written."""
    target = shim_snapshot.path(repo)
    fsio.ensure_ignored_dir(target.parent, comment="ddflow machine-local state: never committed")
    fsio.replace_text(target, text)
    return target
