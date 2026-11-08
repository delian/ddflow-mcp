"""Reading a unified diff for the numbers a suggestion is bounded by. Pure: no git, no I/O.

Counts come from the hunk headers (`@@ -a,b +c,d @@`), so a content line that merely looks like a
file header (`+++ x`), or a commit message above the first `diff --git`, cannot skew them."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FILE = re.compile(
    r'^diff --git '
    r'(?P<a>a/[^\s"]+(?:\s+[^\s"]+)*|"a/(?:[^"\\]|\\.)*") '
    r'(?P<b>b/[^\s"]+(?:\s+[^\s"]+)*|"b/(?:[^"\\]|\\.)*")$'
)
_HUNK = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+\d+(?:,(?P<new>\d+))? @@")
_BINARY = ("GIT binary patch", "Binary files ")
_C_ESCAPES = {
    "\\": "\\",
    '"': '"',
    "t": "\t",
    "n": "\n",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "v": "\v",
    "f": "\f",
}


def _dequote(name: str) -> str:
    """A git `core.quotepath` name, decoded to UTF-8: git C-escapes ``\\``, ``"`` and the C
    escapes, and writes bytes above 0x7f as ``\\ooo`` octal; `shape` un-quotes both (a file
    named ``é`` is ``\\303\\251``). Non-UTF-8 bytes decode with ``surrogateescape`` so two
    distinct raw names stay distinct instead of both collapsing to U+FFFD."""
    raw = bytearray()
    i = 0
    while i < len(name):
        if name[i] != "\\":
            raw.extend(name[i].encode("utf-8", "replace"))
            i += 1
            continue
        if i + 1 >= len(name):
            raw.append(0x5C)  # a trailing lone backslash is a literal backslash
            i += 1
            continue
        nxt = name[i + 1]
        if nxt in _C_ESCAPES:
            raw.extend(_C_ESCAPES[nxt].encode("utf-8"))
            i += 2
            continue
        if (
            i + 4 <= len(name)
            and name[i + 1] in "0123"
            and all(c in "01234567" for c in name[i + 1 : i + 4])
        ):
            raw.append(int(name[i + 1 : i + 4], 8))
            i += 4
            continue
        raw.append(0x5C)
        i += 1
    return raw.decode("utf-8", "surrogateescape")


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
    """One line inside a hunk: (added, removed, old left, new left). A line the header did not
    announce for its side leaves a negative count, which `shape` reads as malformed."""
    if line.startswith("+"):
        return 1, 0, old, new - 1
    if line.startswith("-"):
        return 0, 1, old - 1, new
    if line.startswith("\\"):  # "\ No newline at end of file"
        return 0, 0, old, new
    return 0, 0, old - 1, new - 1  # a context line


def _count(h: re.Match[str], key: str) -> int:
    return 1 if h[key] is None else int(h[key])


class _Reader:
    """The state of one pass over a diff: the files seen, the counts, and the open hunk."""

    def __init__(self) -> None:
        self.files: list[str] = []
        self.added = self.removed = 0
        self.binary = self.malformed = False
        self.in_binary = False
        self.header = False  # between a `diff --git` line and its first hunk
        self.old = self.new = 0  # lines still owed by the open hunk

    @property
    def in_hunk(self) -> bool:
        return self.old > 0 or self.new > 0

    def body(self, line: str) -> None:
        if _FILE.match(line):
            self.malformed = True  # a file header where the hunk header promised more lines
            self.old = self.new = 0
            self.outside(line)
            return
        a, r, self.old, self.new = _body(line, self.old, self.new)
        self.added += a
        self.removed += r
        self.malformed = self.malformed or self.old < 0 or self.new < 0
        self.old, self.new = max(self.old, 0), max(self.new, 0)

    def outside(self, line: str) -> bool:
        """One line outside a hunk; True when the diff ends here (the format-patch signature)."""
        if line == "-- " and self.files:
            return True
        if m := _FILE.match(line):
            self.header, self.in_binary = True, False
            # `core.quotepath` quotes (double quotes + C-escapes) a name only for the side
            # that needs it, so decode that side alone (`diff --git a/foo "b/\303\251"` quotes
            # just `b/`). An unquoted name is a literal POSIX byte string (quotePath=false),
            # e.g. `a/foo bar.txt`, so only a side the header actually quoted is dequoted.
            b_side = m["b"]
            b = _dequote(b_side[3:-1]) if b_side[0] == '"' else b_side[2:]
            if b not in self.files:
                self.files.append(b)
        elif not self.files or self.in_binary:
            pass
        elif line.startswith(_BINARY):
            self.binary = self.in_binary = True  # its base85 body may begin with + or -
        elif self.header and line.startswith(("--- ", "+++ ")):
            pass
        elif h := _HUNK.match(line):
            self.header = False
            self.old, self.new = _count(h, "old"), _count(h, "new")
        elif line[:1] in "+-":
            self.malformed = True
        return False


def _diff_lines(patch: str) -> list[str]:
    # NOTE (LK-sg-events follow-up): the cut at the first `---` line is DELIBERATELY coarse, not a
    # bug. A format-patch message body can itself contain a `---` line, so the cut after it keeps a
    # body `diff --git` snippet and OVER-COUNTs a fake file — the rejection is then by the
    # too-many-files bound, i.e. it fails CLOSED, never under-counts (rubber_duck #27, critic
    # #23/#25). A precise cut would need git's `diffstat` marker, which is not in the text.
    lines = patch.splitlines()
    if lines and lines[0].startswith(("From ", "Subject:", "From:")):
        # a format-patch: the message ends at the first `---` line; a diff quoted in it is text
        cut = next((i for i, ln in enumerate(lines) if ln == "---"), -1)
        return lines[cut + 1 :] if cut >= 0 else []
    return lines


def shape(patch: str) -> Shape:
    """The files, added and removed line counts of a `git diff` / `git format-patch` text."""
    r = _Reader()
    for line in _diff_lines(patch):
        if r.in_hunk:
            r.body(line)
        elif r.outside(line):
            break
    return Shape(tuple(r.files), r.added, r.removed, r.binary, r.malformed)
