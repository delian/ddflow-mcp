"""Reading a unified diff for the numbers a suggestion is bounded by. Pure: no git, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FILE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
BINARY = "GIT binary patch"


@dataclass(frozen=True)
class Shape:
    files: tuple[str, ...]
    added: int
    removed: int
    binary: bool

    @property
    def lines(self) -> int:
        return self.added + self.removed


def shape(patch: str) -> Shape:
    """The files, added and removed line counts of a `git diff` / `git format-patch` text."""
    files: list[str] = []
    added = removed = 0
    in_hunk = False
    for line in patch.splitlines():
        m = _FILE.match(line)
        if m:
            in_hunk = False
            if m["b"] not in files:
                files.append(m["b"])
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+") and not line.startswith("+++ "):
            added += 1
        elif in_hunk and line.startswith("-") and not line.startswith("--- "):
            removed += 1
    return Shape(tuple(files), added, removed, BINARY in patch)
