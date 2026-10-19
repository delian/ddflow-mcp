"""Validate rules about a document's shape: sections, heading order and levels, size."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ..docscheck_types import Finding
from .validate import Ctx, rule


@rule("required_sections")
def required_sections(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """Each section the type marks ``required`` is in the document, at its heading level."""
    for sec in ctx.dt.sections:
        if not sec.required or not sec.heading or r.get("section", sec.id) != sec.id:
            continue
        part = ctx.found[sec.id]
        if part is None:
            yield ctx.finding("required_sections", 1, f"missing section {sec.heading!r}", r)
        elif part.heading.level != sec.level:
            yield ctx.finding(
                "required_sections",
                part.heading.line,
                f"{sec.heading!r} is a level {part.heading.level} heading; the type wants {sec.level}",
                r,
            )


@rule("heading_order")
def heading_order(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """The type's sections that are present follow its order."""
    last = None
    for sec in ctx.dt.sections:
        part = ctx.found.get(sec.id)
        if part is None:
            continue
        if last is not None and part.heading.line < last.heading.line:
            yield ctx.finding(
                "heading_order",
                part.heading.line,
                f"{sec.heading!r} comes before {last.heading.text!r}; the type orders it after",
                r,
            )
        else:
            last = part


@rule("no_skipped_levels")
def no_skipped_levels(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """A heading is at most one level deeper than the one before it."""
    prev = 0
    for h in ctx.doc.headings:
        if prev and h.level > prev + 1:
            yield ctx.finding(
                "no_skipped_levels", h.line, f"{h.text!r} skips from level {prev} to {h.level}", r
            )
        prev = h.level


@rule("unique_headings")
def unique_headings(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """No two headings of the same level read the same."""
    seen: dict[tuple[int, str], int] = {}
    for h in ctx.doc.headings:
        key = (h.level, h.text.casefold())
        if key in seen:
            yield ctx.finding(
                "unique_headings", h.line, f"{h.text!r} repeats the heading on line {seen[key]}", r
            )
        seen.setdefault(key, h.line)


@rule("max_bytes", "limit")
def max_bytes(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    size, limit = len(ctx.doc.text.encode()), r.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool):
        yield ctx.finding("max_bytes", 1, "limit must be an integer", r)
    elif size > limit:
        yield ctx.finding("max_bytes", 1, f"{size} bytes is over the limit of {limit}", r)
