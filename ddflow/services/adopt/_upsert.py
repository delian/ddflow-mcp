"""Inserting or replacing the managed block in a file the project may already own."""

from __future__ import annotations

import re
from pathlib import Path

from ...infra.fsio import NewerContent, RegionError, atomic_write, replace_text
from ._refused import Refused
from ._rules import (
    BLOCK,
    LEGACY_BEGIN,
    LEGACY_END,
    _stamped,
    block_body,
    block_edited,
    block_spans,
    has_legacy_block,
)


def local_edits_path(path: Path) -> Path:
    """Where a hand edit of ``path`` is kept: `<path>.local-edits`, numbered when taken."""
    first = path.with_name(path.name + ".local-edits")
    candidate, n = first, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{first.name}.{n}")
    return candidate


def _section_body(section: str) -> str:
    """The body of a rendered block (`project_section`): what `Managed.splice` takes."""
    body = block_body(section)
    return section if body is None else body


def _begin_lines(text: str) -> int:
    """How many lines open a managed block, in either grammar (paired or not)."""
    begin = re.compile(BLOCK._region().begin or "")
    return sum(
        1
        for line in text.splitlines()
        if begin.fullmatch(line.removesuffix("\r")) or line.removesuffix("\r") == LEGACY_BEGIN
    )


def _collapse_blocks(path: Path, existing: str, section: str, name: str) -> str:
    """Leave exactly ONE managed block, current, where the first one stood; the others go and
    every byte outside the blocks is kept. A block written at a newer format level is never
    rewritten (REFUSED, nothing changes); a hand-edited stamped one is kept aside first."""
    try:
        spans = block_spans(existing)
    except RegionError as exc:
        return Refused(f"SKIPPED {name}: {exc}; fix the markers and re-run adopt")
    want = _section_body(section)
    edited = False
    for lo, hi in spans:
        chunk = existing[lo:hi]
        try:
            BLOCK.splice(chunk, want)
        except NewerContent as exc:
            return Refused(f"REFUSED {name}: {exc}")
        except RegionError:
            pass  # a legacy block: no stamp to be newer
        edited = edited or (block_edited(chunk) and block_body(chunk) != want)
    text = existing
    for lo, hi in reversed(spans[1:]):
        text = text[:lo] + text[hi:]
    first_lo, first_hi = spans[0]
    # the rendered block keeps the attributes a newer writer added at this format level (what
    # the single-block refresh does), taken from the first stamped block
    block = section if section.endswith("\n") else section + "\n"
    for lo, hi in spans:
        if _stamped(existing[lo:hi]):
            block = BLOCK.splice(existing[lo:hi], want)
            break
    text = text[:first_lo] + block + text[first_hi:]
    note = f" (collapsed {len(spans)} blocks into one)"
    if edited:
        aside = local_edits_path(path)
        atomic_write(aside, path.read_bytes())
        note += f" (your edits are kept in {aside.name})"
    replace_text(path, text)
    return f"updated the managed block in {name}{note}"


def _upsert_block(path: Path, section: str, label: str = "") -> str:
    """Insert or replace the managed block, leaving the rest of the file untouched. Messages
    name the file ``label`` (its project path), else its bare name."""
    name = label or path.name
    try:
        existing = path.read_text("utf-8") if path.exists() else ""
    except UnicodeDecodeError:
        return Refused(
            f"SKIPPED {name}: it is not UTF-8 text, so ddflow cannot place its block "
            f"in it safely; convert it to UTF-8 and re-run adopt"
        )
    if _begin_lines(existing) > 1:
        return _collapse_blocks(path, existing, section, name)
    try:
        owned = BLOCK.owns(existing)
    except RegionError as exc:
        return Refused(f"SKIPPED {name}: {exc}; fix the markers and re-run adopt")
    if owned:
        edited = block_edited(existing) and block_body(existing) != _section_body(section)
        try:
            updated = BLOCK.splice(existing, _section_body(section))
        except NewerContent as exc:
            return Refused(f"REFUSED {name}: {exc}")
        note = ""
        if edited:  # a hand edit inside the block: kept, not lost
            aside = local_edits_path(path)
            atomic_write(aside, path.read_bytes())
            note = f" (your edits are kept in {aside.name})"
        replace_text(path, updated)
        return f"updated the managed block in {name}{note}"
    if LEGACY_BEGIN in existing and LEGACY_END in existing and not has_legacy_block(existing):
        return Refused(
            f"SKIPPED {name}: the legacy block markers are reversed; fix them and re-run adopt"
        )
    if has_legacy_block(existing):
        head = existing[: existing.index(LEGACY_BEGIN)]
        tail = existing[existing.index(LEGACY_END) + len(LEGACY_END) :]
        replace_text(path, head + section.strip() + tail)
        return f"updated the managed block in {name}"
    prefix = existing.rstrip() + "\n\n" if existing.strip() else f"# {path.parent.name}\n\n"
    replace_text(path, prefix + section.strip() + "\n")
    return f"{'appended to' if existing.strip() else 'created'} {name}"
