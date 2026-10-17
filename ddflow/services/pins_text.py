"""Three-way merge of a pin file that mixes generated regions with hand-written prose (B86a970f727).

A generated region is a stamped block, `<!-- ddflow:begin NAME ... -->` to `<!-- ddflow:end NAME -->`.
The driver of the git merge and the merge queue's integrator share this one implementation: the
prose around the blocks is merged normally and a real prose conflict stays a conflict; each
block is carried over from our side, because the recipe re-records it right after (the
post-merge hook, or `queue run`'s pin step). A file with no block at all is generated whole:
the hunks that conflict take our side, the rest of both sides is kept, and the recipe decides.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..infra import fsio
from ..infra import git as G

# One conflict hunk of `git merge-file -L ours -L base -L theirs` (no diff3 base section).
_HUNK = re.compile(r"<<<<<<< ours\n(.*?)=======\n(.*?)>>>>>>> theirs\n", re.S)
# A number the README states as a count of generated things ("320 knobs", "5 of the 320").
_DIGITS = re.compile(
    r"\d+(?= (?:knobs|tools|commands|sections|flags)\b)|(?<=of the )\d+(?=\)| knobs\b)"
)


def _counts_only(m: re.Match[str]) -> str:
    """A hunk whose two sides differ only in a stated count of generated things ("320 knobs" against
    "323 knobs"): our side, which the recipe re-records after the merge (Be71986b1d3)."""
    ours, theirs = m.group(1), m.group(2)
    same = _DIGITS.sub("#", ours) == _DIGITS.sub("#", theirs)
    return ours if same else m.group(0)


_BLOCK = re.compile(r"<!-- ddflow:begin (\S+)[^\n]*?-->.*?<!-- ddflow:end \1 -->", re.S)


def _hole(name: str) -> str:
    return f"<<<ddflow-generated {name}>>>"


def _blocks(text: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in _BLOCK.finditer(text)}


def _holed(text: str) -> str:
    return _BLOCK.sub(lambda m: _hole(m.group(1)), text)


def _merge_file(files: tuple[bytes, bytes, bytes], *flags: str) -> tuple[int, bytes]:
    """`git merge-file` over (ours, base, theirs): (conflicts, merged); conflicts < 0 when git
    cannot merge them (binary content)."""
    with fsio.scratch_dir("ddflow-pt.") as d:
        for name, data in zip(("ours", "base", "theirs"), files, strict=True):
            (d / name).write_bytes(data)
        r = G.run(d, "merge-file", "-q", *flags, "ours", "base", "theirs")
        merged = (d / "ours").read_bytes()
        # A failure to merge (binary) exits 255, which no count of conflicts is: the count is
        # believed only when the conflict markers are there.
        code = r.code if r.code == 0 or (r.code > 0 and b"\n=======\n" in merged) else -1
        return (-1 if r.err else code), merged


def merge_pin_text(ours: bytes, base: bytes, theirs: bytes) -> tuple[bytes, bool]:
    """(result, conflicted): the pin file's three versions merged. ``conflicted`` is true when
    the result holds conflict markers because of prose both sides edited; content git cannot
    merge at all (binary) keeps our side."""
    try:
        o, b, t = (x.decode("utf-8") for x in (ours, base, theirs))
    except UnicodeDecodeError:  # no blocks can be told apart: merge the bytes, conflicts stay
        code, out = _merge_file((ours, base, theirs))
        return (ours, False) if code < 0 else (out, code > 0)
    if not (_blocks(o) or _blocks(b) or _blocks(t)):
        code, out = _merge_file((ours, base, theirs), "--ours")
        return (ours, False) if code < 0 else (out, False)
    code, out = _merge_file(
        tuple(_holed(x).encode() for x in (o, b, t)), "-L", "ours", "-L", "base", "-L", "theirs"
    )  # type: ignore[arg-type]
    if code < 0:
        return ours, False
    text = out.decode("utf-8", errors="replace")
    if code > 0:
        text = _HUNK.sub(_counts_only, text)
        code = text.count("<<<<<<< ours\n")
    kept = {**_blocks(b), **_blocks(t), **_blocks(o)}
    for name, block in kept.items():
        text = text.replace(_hole(name), block)
    return text.encode(), code > 0


def merge_into(ours: Path, base: Path, theirs: Path) -> bool:
    """The git merge driver's job on the three files it is given: the result replaces ``ours``.
    True when a prose conflict remains in it."""
    result, conflicted = merge_pin_text(ours.read_bytes(), base.read_bytes(), theirs.read_bytes())
    ours.write_bytes(result)
    return conflicted
