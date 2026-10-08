"""The one reader of a unified diff's file headers (D-unify: one meaning per git query).

Three modules used to read ``diff --git`` / ``---`` / ``+++`` / ``rename`` lines their own
way (the review chunker, the doc-sync reader, the worktree coverage check). They now share
these primitives, so a path git quotes, a name holding a space and a rename mean the same
thing everywhere.

A header line cannot always be split into its two paths (``diff --git a/x b/y z b/y z``),
so a file's paths are read from the ``---``/``+++``/``rename`` lines, which name ONE path
each, and only from the ``diff --git`` line when a file has none (a mode change, a binary
file). Lines past a file's first ``@@`` are content and never headers.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterator
from dataclasses import dataclass


def unquote(raw: str) -> str:
    """A diff header path. git C-quotes a name holding a quote, backslash, control character
    (or any non-ASCII byte, under the default ``core.quotePath``); undo that rather than
    miss the file. A name git left unquoted is returned as it is. Bytes that are not UTF-8
    come back as surrogate escapes, the way ``os.fsdecode`` spells them."""
    if len(raw) > 1 and raw[0] == raw[-1] == '"':
        try:
            return codecs.escape_decode(raw[1:-1].encode("utf-8", "surrogateescape"))[0].decode(
                "utf-8", "surrogateescape"
            )
        except ValueError:
            return raw[1:-1]
    return raw


def header_path(line: str, prefix: str) -> str | None:
    """The path on a ``--- ``/``+++ `` line, minus ``prefix`` (``a/``/``b/``) when it has
    it; None for ``/dev/null`` (a file added or deleted)."""
    rest = line[4:].rstrip("\n").rstrip("\t")
    if rest == "/dev/null":
        return None
    rest = unquote(rest)
    return rest[len(prefix) :] if rest.startswith(prefix) else rest


@dataclass(frozen=True)
class FileDiff:
    """One ``diff --git`` section's file."""

    header: str  #: the ``diff --git`` line
    old: str | None  #: the path before the change; None for an added file
    new: str | None  #: the path after it; None for a deleted file

    @property
    def path(self) -> str:
        """The file the section changes: where it ends up, else where it was."""
        return self.new or self.old or ""

    @property
    def paths(self) -> tuple[str, ...]:
        """Every path the section names (both sides of a rename), each once."""
        return tuple(dict.fromkeys(p for p in (self.old, self.new) if p))


def _from_header_line(head: str) -> tuple[str | None, str | None]:
    pair = head[len("diff --git ") :]
    quoted = re.findall(r'"(?:[^"\\]|\\.)*"', pair)
    if quoted:
        old = header_path(f"--- {quoted[0]}", "a/") if len(quoted) > 1 else None
        return old, header_path(f"+++ {quoted[-1]}", "b/")
    # "a/X b/X", or "X X" under diff.noprefix: two halves naming the same file.
    half = len(pair) // 2
    if len(pair) % 2 == 1 and pair[half] == " ":
        left, right = pair[:half], pair[half + 1 :]
        if left.startswith("a/") and right.startswith("b/") and left[2:] == right[2:]:
            return left[2:], left[2:]
        if left == right:
            return left, left
    return None, pair


def parse_section(section: str) -> FileDiff:
    """The `FileDiff` of one ``diff --git`` section (its header line first)."""
    head, *rest = section.split("\n")
    old = new = None
    renamed_to = renamed_from = None
    seen = False
    for line in rest:
        if line.startswith("@@"):
            break
        if line.startswith("rename to ") or line.startswith("copy to "):
            renamed_to = unquote(line.split(" to ", 1)[1].rstrip("\t"))
            seen = True
        elif line.startswith("rename from ") or line.startswith("copy from "):
            renamed_from = unquote(line.split(" from ", 1)[1].rstrip("\t"))
            seen = True
        elif line.startswith("--- "):
            old = header_path(line, "a/")
            seen = True
        elif line.startswith("+++ "):
            new = header_path(line, "b/")
            seen = True
    if not seen:
        old, new = _from_header_line(head)
        return FileDiff(head, old, new)
    # A pure rename has no ---/+++ lines; an added/deleted file has one /dev/null side.
    deleted = any(ln.startswith("deleted file mode") for ln in rest if not ln.startswith("@@"))
    added = any(ln.startswith("new file mode") for ln in rest if not ln.startswith("@@"))
    old = old or renamed_from or (None if added else new)
    new = new or renamed_to or (None if deleted else old)
    return FileDiff(head, None if added else old, None if deleted else new)


def sections(diff: str) -> Iterator[str]:
    """Each ``diff --git`` section of ``diff`` as text."""
    for part in re.split(r"(?m)^(?=diff --git )", diff):
        if part.startswith("diff --git "):
            yield part


def files(diff: str) -> list[FileDiff]:
    """Every file ``diff`` changes, in order."""
    return [parse_section(s) for s in sections(diff)]


def headers(diff: str) -> list[str]:
    """Every ``diff --git`` line of ``diff``."""
    return re.findall(r"(?m)^diff --git .*$", diff)


def touched_paths(diff: str) -> set[str]:
    """Every path any file section of ``diff`` names (both sides of a rename)."""
    return {p for f in files(diff) for p in f.paths}
