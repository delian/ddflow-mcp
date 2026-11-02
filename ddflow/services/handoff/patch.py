"""Reading a unified diff for the numbers a suggestion is bounded by. Pure: no git, no I/O.

Counts come from the hunk headers (`@@ -a,b +c,d @@`), so a content line that merely looks like a
file header (`+++ x`), or a commit message above the first `diff --git`, cannot skew them."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FILE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+\d+(?:,(?P<new>\d+))? @@")
_BINARY = ("GIT binary patch", "Binary files ")


@dataclass(frozen=True)
class Shape:
    files: tuple[str, ...]
    added: int
    removed: int
    binary: bool
    #: +/- lines outside any hunk the headers announced: a header that understates its hunk
    malformed: bool = False

    @property
    def lines(self) -> int:
        return self.added + self.removed


def _body(line: str, old: int, new: int) -> tuple[int, int, int, int]:
    """One line inside a hunk: (added, removed, old left, new left)."""
    if line.startswith("+"):
        return 1, 0, old, new - 1
    if line.startswith("-"):
        return 0, 1, old - 1, new
    if line.startswith("\\"):  # "\ No newline at end of file"
        return 0, 0, old, new
    return 0, 0, old - 1, new - 1  # a context line


def _count(h: re.Match[str], key: str) -> int:
    return 1 if h[key] is None else int(h[key])


def shape(patch: str) -> Shape:
    """The files, added and removed line counts of a `git diff` / `git format-patch` text."""
    files: list[str] = []
    added = removed = 0
    binary = malformed = False
    header = False  # between a `diff --git` line and its first hunk
    old = new = 0  # lines still owed by the open hunk
    for line in patch.splitlines():
        if old > 0 or new > 0:
            a, r, old, new = _body(line, old, new)
            added, removed = added + a, removed + r
        elif line == "-- ":
            break  # the format-patch signature: nothing after it is diff
        elif m := _FILE.match(line):
            header = True
            if m["b"] not in files:
                files.append(m["b"])
        elif files and line.startswith(_BINARY):
            binary = True
        elif header and line.startswith(("--- ", "+++ ")):
            pass
        elif files and (h := _HUNK.match(line)):
            header = False
            old, new = _count(h, "old"), _count(h, "new")
        elif files and line[:1] in "+-":
            malformed = True
    return Shape(tuple(files), added, removed, binary, malformed)
