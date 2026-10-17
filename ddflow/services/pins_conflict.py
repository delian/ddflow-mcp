"""Conflict markers a merge left in a pin file (DX-pins-resolve-readme-conflict).

A pin file is regenerated, so a conflict inside what the generators own is not worth reading:
the hunk is settled by taking our side, and the recipe that follows re-records it. A conflict in
the hand-written prose of a file that mixes prose with generated regions is not the
generators' to settle: it is reported with its lines and the file is left as it is. A file
with no generated region at all (a snapshot, a transcript) is generated whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.fsio import replace_text

_BEGIN = re.compile(r"<!-- ddflow:begin (\S+)")  # at the start of a line: prose may quote it
_END = re.compile(r"<!-- ddflow:end (\S+) -->")


@dataclass
class Hunk:
    start: int  # 1-based line of the `<<<<<<<` marker
    ours: list[str]
    theirs: list[str]
    owned: bool = False


@dataclass
class Settled:
    """What was done to one pin file: ``text`` is the file with our side taken (``None``
    when nothing was settled), ``refused`` the lines of the prose conflicts that were not."""

    path: str
    file: Path | None = None
    hunks: int = 0
    text: str | None = None
    refused: list[int] = field(default_factory=list)
    stray: bool = False  # the refusal is markers that do not pair up, not a prose conflict


def _parse(lines: list[str], i: int) -> tuple[int, Hunk] | None:
    """The conflict whose `<<<<<<<` line is ``i``: (index of its `>>>>>>>` line, hunk), or None
    when it is not closed (a new one opens first, or no `=======` and `>>>>>>>` follow)."""
    ours: list[str] = []
    theirs: list[str] = []
    side, sep = ours, False
    for j in range(i + 1, len(lines)):
        ln = lines[j].rstrip("\n")
        if ln.startswith("<<<<<<< "):
            return None
        if ln.startswith("||||||| ") and not sep:
            side = []  # the base section of a diff3 conflict: part of neither side
        elif ln == "=======" and not sep:
            side, sep = theirs, True
        elif ln.startswith(">>>>>>> ") and sep:
            return j, Hunk(i + 1, ours, theirs)
        else:
            side.append(lines[j])
    return None


def hunks_of(lines: list[str]) -> list[tuple[int, int, Hunk]]:
    """(first, last, hunk) for each complete conflict in ``lines``: indexes of the `<<<<<<<`
    and `>>>>>>>` lines. A lone `=======` (a setext heading) or an unclosed marker is no
    conflict."""
    out: list[tuple[int, int, Hunk]] = []
    i = 0
    while i < len(lines):
        parsed = _parse(lines, i) if lines[i].startswith("<<<<<<< ") else None
        if parsed is None:
            i += 1
            continue
        out.append((i, parsed[0], parsed[1]))
        i = parsed[0] + 1
    return out


def _owned(lines: list[str], inside: bool) -> tuple[bool, bool]:
    """(every line is a generated region's, whether a region is open at the end) for one side
    of a hunk that starts ``inside`` a region or not."""
    ok = True
    for ln in lines:
        if _BEGIN.match(ln):
            inside = True
        elif _END.match(ln):
            inside = False  # the end marker belongs to the region it closes
        elif not inside:
            ok = False
    return ok, inside


def settle(path: str, text: str) -> Settled:
    """Settle the conflicts of one pin file's ``text`` by taking our side where the generated
    regions own them; the prose conflicts that are not are listed in ``refused``."""
    res = Settled(path)
    lines = text.splitlines(keepends=True)
    found = hunks_of(lines)
    res.hunks = len(found)
    # A marker no complete conflict accounts for (a nested or an unclosed one) means the file was
    # merged over unresolved markers: no hunk of it is ours to settle.
    known = {i for first, last, _ in found for i in (first, last)}
    res.refused = [
        n + 1
        for n, ln in enumerate(lines)
        if ln.startswith(("<<<<<<< ", ">>>>>>> ")) and n not in known
    ]
    res.stray = bool(res.refused)
    if res.refused or not found:
        return res
    mixed = any(_BEGIN.match(ln) for ln in lines)
    out: list[str] = []
    pos = 0
    inside = False
    for first, last, h in found:
        inside = _owned(lines[pos:first], inside)[1]
        a, end_a = _owned(h.ours, inside)
        b, end_b = _owned(h.theirs, inside)
        h.owned = (not mixed) or (a and b and end_a == end_b)
        if not h.owned:
            res.refused.append(h.start)
        out += lines[pos:first] + h.ours
        pos = last + 1
        inside = end_a
    if res.refused:
        return res
    res.text = "".join(out + lines[pos:])
    return res


def take_side(text: str, side: str) -> str:
    """``text`` with every conflict settled to one side: ``"ours"`` or ``"theirs"``."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    for first, last, h in hunks_of(lines):
        out += lines[pos:first] + (h.ours if side == "ours" else h.theirs)
        pos = last + 1
    return "".join(out + lines[pos:])


def settle_files(repo: Path, files: list[str]) -> list[Settled]:
    """`settle` for each pin file (a directory: every file in it) that holds conflict markers.
    Nothing is written: `apply` does that, once no file is left that needs a person."""
    done: list[Settled] = []
    for f in files:
        base = Path(repo) / f
        paths = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        for p in paths:
            try:
                text = p.read_text("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "<<<<<<< " not in text and ">>>>>>> " not in text:
                continue
            res = settle(p.relative_to(repo).as_posix(), text)
            res.file = p
            if res.hunks or res.refused:
                done.append(res)
    return done


def apply(settled: list[Settled]) -> None:
    """Write the settled files."""
    for s in settled:
        if s.file is not None and s.text is not None:
            replace_text(s.file, s.text)
