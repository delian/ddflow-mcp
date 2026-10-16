"""Do the files a task declares exist? (DX-plan-globs-exist)

A literal glob (no wildcard) that names nothing is usually a typo or a file that moved: the
task then holds a lease on a path nobody writes, and the file it really edits is
unprotected. But a task also CREATES files, so a missing file alone is not a finding. One
is reported when the glob's DIRECTORY does not exist either, or when the name is
LIKE an existing one -- the same name elsewhere, or a close name beside it.

Pure: ``exists`` and ``files`` (the repository's files) are passed in.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from posixpath import basename, dirname

_WILD = ("*", "?", "[")
#: How alike two file names must be to read as the same file, mistyped.
SIBLING_CUTOFF = 0.8
#: How many nearest paths a finding names.
NEAREST = 3


@dataclass(frozen=True)
class Missing:
    glob: str
    why: str
    nearest: list[str] = field(default_factory=list)

    def text(self) -> str:
        near = f"; nearest: {', '.join(self.nearest)}" if self.nearest else ""
        return f"{self.glob!r} {self.why}{near}"


def literal(glob: str) -> bool:
    """Does the glob name one path, rather than a pattern?"""
    return bool(glob) and not any(w in glob for w in _WILD)


def missing(
    globs: Iterable[str], *, exists: Callable[[str], bool], files: Iterable[str]
) -> list[Missing]:
    """The literal globs that name nothing, where that is more than a new file."""
    files = list(files)
    names: dict[str, list[str]] = {}
    for f in files:
        names.setdefault(basename(f), []).append(f)
    out: list[Missing] = []
    for raw in dict.fromkeys(globs):
        g = raw.rstrip("/")
        if not literal(g) or exists(g):
            continue
        d, name = dirname(g), basename(g)
        if d and not exists(d):
            near = difflib.get_close_matches(g, files, n=NEAREST, cutoff=0.5)
            out.append(Missing(g, "does not exist, nor does its directory", near))
        elif names.get(name):
            out.append(Missing(g, "does not exist here but does elsewhere", names[name][:NEAREST]))
        else:
            beside = {basename(f): f for f in files if dirname(f) == d}
            close = difflib.get_close_matches(name, list(beside), n=NEAREST, cutoff=SIBLING_CUTOFF)
            if close:
                out.append(
                    Missing(
                        g,
                        "does not exist, but a file named like it does",
                        [beside[c] for c in close],
                    )
                )
    return out
