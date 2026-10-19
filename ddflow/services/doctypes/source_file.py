"""``file:<path>[#anchor]``: a tracked file, or the one section under a heading of it.

The anchor is a GitHub heading anchor (``#install``). The section runs from its heading to the
next heading of the same or a higher level, found by the document chunker's own scanner (so a
``#`` inside a code fence is not a heading). A path outside the repo, an untracked file, one
over the size cap and an anchor that matches nothing are each refused or unavailable, never an
empty section.
"""

from __future__ import annotations

from typing import Any

from ...core import docchunk
from ...core.docchunk_scan import SCANNERS
from ...core.slug import github_anchor
from .sources import (
    Context,
    SourceError,
    SourceKind,
    check_relative,
    read_capped,
    register,
    tracked_in,
)


def _split(arg: str) -> tuple[str, str]:
    path, _, anchor = arg.partition("#")
    return path, anchor


def _check(arg: str) -> str:
    path, _ = _split(arg)
    check_relative(path, "file")
    return arg


def section_of(text: str, path: str, anchor: str) -> str:
    """The lines under the heading whose GitHub anchor is ``anchor`` (heading included)."""
    fmt = docchunk.detect(path)
    lines = text.splitlines()
    scan = SCANNERS[fmt](lines, 0) if fmt in SCANNERS else None
    heads = scan.heads if scan else []
    for n, (line, level, title) in enumerate(heads):
        if github_anchor(title) != anchor:
            continue
        stop = next((h[0] for h in heads[n + 1 :] if h[1] <= level), len(lines))
        return "\n".join(lines[line:stop]).rstrip() + "\n"
    raise SourceError(f"{path} has no heading with the anchor #{anchor}")


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    path, anchor = _split(arg)
    if path not in tracked_in(ctx, path):
        raise SourceError(f"{path} is not a tracked file of the repo")
    text = read_capped(ctx, path)
    return {"text": section_of(text, path, anchor) if anchor else text, "path": path}


register(SourceKind("file", _check, _data))
