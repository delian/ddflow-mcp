"""Validate rules about what a section says: text, size, code, tables, lists, links."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import unquote

from ..docscheck_md import anchors_of
from ..docscheck_types import Finding
from .validate import Ctx, regex, rule

_ASK = re.compile(r"<!--\s*ask\b|\{\{\s*ask\b|\[ASK\b", re.I)
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def _required(kind: str, ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    rx, bad = regex(ctx, r)
    yield from bad
    if rx and ctx.scope(r) and not rx.search("\n".join(t for _, t in ctx.prose(r))):
        yield ctx.finding(kind, 1, f"no text matches /{rx.pattern}/", r)


@rule("required_text", "regex")
def required_text(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    yield from _required("required_text", ctx, r)


@rule("pattern", "regex")
def pattern(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """The document (or section) contains text matching ``regex``."""
    yield from _required("pattern", ctx, r)


@rule("forbidden_text", "regex")
def forbidden_text(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    rx, bad = regex(ctx, r)
    yield from bad
    for n, line in ctx.prose(r) if rx else []:
        if rx.search(line):
            yield ctx.finding("forbidden_text", n, f"text matching /{rx.pattern}/", r)


@rule("forbidden_whole_text", "regex")
def forbidden_whole_text(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """A vacuous assertion: a regex over the whole prose of the scope, across lines."""
    rx, bad = regex(ctx, r)
    yield from bad
    rows = ctx.prose(r) if rx else []
    m = rx.search("\n".join(t for _, t in rows)) if rx else None
    if m and rows:
        line = rows[_row_at(rows, m.start())][0]
        yield ctx.finding("forbidden_whole_text", line, f"text matching /{rx.pattern}/", r)


def _row_at(rows: list[tuple[int, str]], offset: int) -> int:
    """The index in ``rows`` of the line holding ``offset`` of their newline-joined text."""
    pos = 0
    for i, (_, t) in enumerate(rows):
        pos += len(t) + 1
        if offset < pos:
            return i
    return max(len(rows) - 1, 0)


def _bound(kind: str, ctx: Ctx, r: Mapping[str, Any], want_max: bool) -> Iterator[Finding]:
    limit = r.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool):
        yield ctx.finding(kind, 1, "limit must be an integer", r)
    elif part := ctx.scope(r):
        words, line = part.words, max(part.heading.line, 1)
        if want_max and words > limit:
            yield ctx.finding(kind, line, f"{words} words is over {limit}", r)
        elif not want_max and words < limit:
            yield ctx.finding(kind, line, f"{words} words is under {limit}", r)


@rule("max_words", "limit")
def max_words(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    yield from _bound("max_words", ctx, r, True)


@rule("min_words", "limit")
def min_words(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    yield from _bound("min_words", ctx, r, False)


@rule("code_blocks", "lang", "min")
def code_blocks(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    if part := ctx.scope(r):
        want, need = r.get("lang"), r.get("min", 1)
        if not isinstance(need, int) or isinstance(need, bool):
            yield ctx.finding("code_blocks", 1, "min must be an integer", r)
            return
        have = [x for x in part.code_langs if not want or x == want]
        if len(have) < need:
            lang = f" in {want}" if want else ""
            yield ctx.finding(
                "code_blocks",
                max(part.heading.line, 1),
                f"{len(have)} code block(s){lang}; wants {need}",
                r,
            )


@rule("has_table")
def has_table(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    if (part := ctx.scope(r)) and not part.tables:
        yield ctx.finding("has_table", max(part.heading.line, 1), "no table", r)


@rule("has_list")
def has_list(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    if (part := ctx.scope(r)) and not part.items:
        yield ctx.finding("has_list", max(part.heading.line, 1), "no list", r)


@rule("each_item_matches", "regex")
def each_item_matches(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """Every list item of the section matches ``regex`` (e.g. each expectation names its falsifier)."""
    rx, bad = regex(ctx, r)
    yield from bad
    part = ctx.scope(r)
    for item in part.items if rx and part else []:
        if not rx.search(item):
            yield ctx.finding(
                "each_item_matches",
                part.heading.line,
                f"item {item[:60]!r} does not match /{rx.pattern}/",
                r,
            )


@rule("no_ask_placeholder")
def no_ask_placeholder(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """No question to the user is left open (an ``<!-- ask: ... -->`` marker, which a prose
    check would skip as code or comment, so every line of the scope is read)."""
    if part := ctx.scope(r):
        for n, line in enumerate(ctx.doc.text.splitlines(), 1):
            if part.heading.line <= n <= part.end and _ASK.search(line):
                yield ctx.finding("no_ask_placeholder", n, "an open ask placeholder", r)


@rule("links_resolve")
def links_resolve(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    """Local links in the scope reach a tracked file and, when they name one, a heading."""
    if (part := ctx.scope(r)) is None:
        return
    if ctx.read is None:
        yield ctx.finding("links_resolve", 1, "cannot tell: no way to read the tree", r)
        return
    for n, url in part.links:
        if _EXTERNAL.match(url):
            continue
        target, _, frag = unquote(url).partition("#")
        dest = (
            posixpath.normpath(posixpath.join(posixpath.dirname(ctx.path), target))
            if target
            else ctx.path
        )
        text = ctx.doc.text if not target else ctx.read(dest)
        if text is None:
            yield ctx.finding("links_resolve", n, f"{url}: {dest} is not in the tree", r)
        elif frag and dest.endswith(".md") and frag.lower() not in anchors_of(text):
            yield ctx.finding("links_resolve", n, f"{url}: no heading #{frag} in {dest}", r)
