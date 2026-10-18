"""Heading-aware document chunker: Markdown, reStructuredText, AsciiDoc and plain text.

A chunk is a run of lines of one section, of one kind (prose, code, table) and one region:
``(path, chunk_id = path#anchor~ordinal, heading_path, line range, text, sha256, kind,
region, ownership, doctype)``. Splits on headings, then on blocks past ``max_chars`` with
``overlap`` characters carried between prose chunks; never inside a code fence (an oversize
fence is one oversize chunk). Deterministic: the same file gives the same chunks and ids, and
an edit changes only the touched chunks' hashes. Front matter becomes metadata.

Pure, stdlib. The caller scans ddflow markers (`services.export.regions`) and passes them as
`RegionSpan`s; this module does not read the marker grammar a second time.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .digest import hasher
from .slug import github_anchor

FORMATS = {".md": "md", ".markdown": "md", ".rst": "rst", ".adoc": "adoc", ".asciidoc": "adoc"}
MAX_CHARS = 1500
OVERLAP = 150

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_RST_ADORN = re.compile(r"^([!-/:-@\[-`{-~])\1+\s*$")
_ADOC_HEAD = re.compile(r"^(={1,6})[ \t]+(\S.*?)\s*$")
_ADOC_BLOCK = re.compile(r"^(-{4,}|\.{4,})\s*$")
_LINES = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class RegionSpan:
    """A ddflow region, as the caller found it: its lines, markers included (1-based)."""

    doc: str
    name: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Chunk:
    path: str
    chunk_id: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    text: str
    sha256: str
    kind: str = "prose"  # prose | code | table
    region: str = ""  # "doc/name" inside ddflow markers
    ownership: str = "human"  # generated inside a region
    doctype: str = ""


@dataclass(frozen=True)
class Chunked:
    chunks: tuple[Chunk, ...] = ()
    meta: dict[str, str] = field(default_factory=dict)  # front matter
    skipped: str = ""  # why a file gave no chunks (not UTF-8)


def detect(path: str) -> str:
    """The format a path's extension names; anything else is plain text."""
    dot = path.rfind(".")
    return FORMATS.get(path[dot:].lower(), "text") if dot >= 0 else "text"


def _front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for end, ln in enumerate(lines[1:], 1):
        if ln.strip() in ("---", "..."):
            meta = {}
            for row in lines[1:end]:
                key, sep, val = row.partition(":")
                if sep and key.strip() and not key.startswith((" ", "\t", "#")):
                    meta[key.strip()] = val.strip().strip("\"'")
            return meta, end + 1
    return {}, 0


@dataclass
class _Scan:
    heads: list[tuple[int, int, str]] = field(default_factory=list)  # line, level, title
    code: list[tuple[int, int]] = field(default_factory=list)  # [first, last] line indexes
    table: list[tuple[int, int]] = field(default_factory=list)


def _scan_md(lines: list[str], start: int) -> _Scan:
    sc, i, n = _Scan(), start, len(lines)
    while i < n:
        ln = lines[i]
        if m := _FENCE.match(ln):
            ch, size, j = m.group(1)[0], len(m.group(1)), i + 1
            close = re.compile(rf"^ {{0,3}}{re.escape(ch)}{{{size},}}[ \t]*$")
            while j < n and not close.match(lines[j]):
                j += 1
            sc.code.append((i, min(j, n - 1)))
            i = j + 1
        elif m := _ATX.match(ln):
            sc.heads.append((i, len(m.group(1)), (m.group(2) or "").strip()))
            i += 1
        elif (
            ln.strip()
            and i + 1 < n
            and (i == start or not lines[i - 1].strip())
            and (s := _SETEXT.match(lines[i + 1]))
            and not ln.lstrip().startswith(("|", ">", "-", "*", "+"))
        ):
            sc.heads.append((i, 1 if s.group(1)[0] == "=" else 2, ln.strip()))
            i += 2
        elif ln.lstrip().startswith("|") and i + 1 < n and lines[i + 1].lstrip().startswith("|"):
            j = i
            while j + 1 < n and lines[j + 1].lstrip().startswith("|"):
                j += 1
            sc.table.append((i, j))
            i = j + 1
        else:
            i += 1
    return sc


def _scan_rst(lines: list[str], start: int) -> _Scan:
    sc, styles, i, n = _Scan(), [], start, len(lines)
    while i < n:
        a, over = lines[i], False
        if (
            _RST_ADORN.match(a)
            and i + 2 < n
            and lines[i + 1].strip()
            and lines[i + 2].startswith(a[0])
        ):
            over, i0, text, under = True, i, lines[i + 1].strip(), lines[i + 2]
        elif a.strip() and not _RST_ADORN.match(a) and i + 1 < n and _RST_ADORN.match(lines[i + 1]):
            i0, text, under = i, a.strip(), lines[i + 1]
        else:
            i += 1
            continue
        if under[0] != (a[0] if over else lines[i + 1][0]) and over:
            i += 1
            continue
        if len(under.rstrip()) >= len(text):
            style = (under[0], over)
            if style not in styles:
                styles.append(style)
            sc.heads.append((i0, styles.index(style) + 1, text))
            i += 3 if over else 2
        else:
            i += 1
    return sc


def _scan_adoc(lines: list[str], start: int) -> _Scan:
    sc, i, n = _Scan(), start, len(lines)
    while i < n:
        ln = lines[i]
        if (m := _ADOC_BLOCK.match(ln)) or ln.strip() == "|===":
            close = m.group(1) if m else "|==="
            j = i + 1
            while j < n and lines[j].strip() != close:
                j += 1
            (sc.code if m else sc.table).append((i, min(j, n - 1)))
            i = j + 1
        elif m := _ADOC_HEAD.match(ln):
            sc.heads.append((i, len(m.group(1)), m.group(2)))
            i += 1
        else:
            i += 1
    return sc


_SCANNERS = {"md": _scan_md, "rst": _scan_rst, "adoc": _scan_adoc}


def _sections(lines: list[str], start: int, sc: _Scan):
    """``(heading_path, anchor, first, stop)`` per section; text before a heading has no anchor."""
    out, stack, seen, used = [], [], {}, set()
    first = start
    cur: tuple[tuple[str, ...], str] = ((), "")
    for at, level, title in sc.heads:
        if at > first and any(ln.strip() for ln in lines[first:at]):
            out.append((*cur, first, at))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        slug = github_anchor(title) or "section"
        anchor, n = slug, seen.get(slug, 0)
        while anchor in used:  # GitHub's slugger: bump until the anchor is free
            n += 1
            anchor = f"{slug}-{n}"
        seen[slug] = n
        used.add(anchor)
        cur = (tuple(t for _, t in stack), anchor)
        first = at
    if first < len(lines) and any(ln.strip() for ln in lines[first:]):
        out.append((*cur, first, len(lines)))
    return out


def _kind_at(sc: _Scan, i: int) -> tuple[str, int]:
    """The block kind line ``i`` starts and the last line of that block (prose: -1)."""
    for kind, spans in (("code", sc.code), ("table", sc.table)):
        for a, b in spans:
            if a == i:
                return kind, b
    return "prose", -1


def _blocks(lines, first, stop, sc, regions):
    """``(a, b_exclusive, kind, region_index)`` blocks of a section, blank lines dropped; a
    block never crosses a code block, a table or a region boundary."""
    cuts = {r.line_start - 1 for r in regions} | {r.line_end for r in regions}
    out, i = [], first
    while i < stop:
        if not lines[i].strip():
            i += 1
            continue
        kind, last = _kind_at(sc, i)
        j = last + 1 if kind != "prose" else i + 1
        while kind == "prose" and j < stop and lines[j].strip() and j not in cuts:
            if _kind_at(sc, j)[0] != "prose":
                break
            j += 1
        out.append((i, min(j, stop), kind, _region_of(regions, i)))
        i = j
    return out


def _region_of(regions: Sequence[RegionSpan], i: int) -> int:
    for k, r in enumerate(regions):
        if r.line_start - 1 <= i <= r.line_end - 1:
            return k
    return -1


def _cut(text: str, size: int) -> list[str]:
    return [text[k : k + size] for k in range(0, len(text), size)] or [""]


def _pack(lines, blocks, max_chars, overlap):
    """``(a, b_exclusive, kind, region, text)`` chunks: blocks of one kind and region
    packed up to ``max_chars``; an oversize prose block is cut at line (then character)
    boundaries, with the last whole lines of a cut carried into the next chunk."""
    out: list[tuple[int, int, str, int, str]] = []
    cur: list[tuple[int, int, str, int]] = []

    def flush() -> None:
        if cur:
            a, b = cur[0][0], cur[-1][1]
            out.append((a, b, cur[0][2], cur[0][3], "\n".join(lines[a:b])))
            cur.clear()

    for blk in blocks:
        a, b, kind, reg = blk
        size = len("\n".join(lines[a:b]))
        if kind == "prose" and size > max_chars:
            if cur and cur[0][2:] == (kind, reg):  # a lone heading stays with its body
                a = cur[0][0]
                cur.clear()
            else:
                flush()
            out.extend(_split_prose(lines, a, b, reg, max_chars, overlap))
            continue
        if cur and (cur[0][2:] != (kind, reg) or len("\n".join(lines[cur[0][0] : b])) > max_chars):
            flush()
        cur.append(blk)
    flush()
    return out


def _split_prose(lines, a, b, reg, max_chars, overlap):
    out, s = [], a
    while s < b:
        e, total = s, 0
        while e < b and (e == s or total + 1 + len(lines[e]) <= max_chars):
            total += len(lines[e]) + (1 if e > s else 0)
            e += 1
        if e == s + 1 and len(lines[s]) > max_chars:
            out.extend((s, e, "prose", reg, p) for p in _cut(lines[s], max_chars))
        else:
            out.append((s, e, "prose", reg, "\n".join(lines[s:e])))
        if e >= b:
            break
        back, size = e, 0  # overlap: whole lines from the end of this chunk, at most `overlap`
        while back - 1 > s and size + len(lines[back - 1]) + 1 <= overlap:
            back -= 1
            size += len(lines[back]) + 1
        s = back if back < e and back > s else e
    return out


def chunk(
    path: str,
    text: str | bytes,
    *,
    doctype: str = "",
    regions: Sequence[RegionSpan] = (),
    max_chars: int = MAX_CHARS,
    overlap: int = OVERLAP,
    fmt: str = "",
) -> Chunked:
    """The chunks of ``text`` (any line ending; bytes are decoded as UTF-8, and a file that is
    not UTF-8 is skipped and says so). ``fmt`` defaults to what ``path`` names."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            return Chunked(skipped=f"{path} is not UTF-8 (byte {exc.start}); skipped")
    fmt = fmt or detect(path)
    lines = _LINES.split(text.removeprefix("﻿"))
    meta, start = _front_matter(lines) if fmt == "md" else ({}, 0)
    sc = _SCANNERS[fmt](lines, start) if fmt in _SCANNERS else _Scan()
    sections = _sections(lines, start, sc) if sc.heads else [((), "", start, len(lines))]
    chunks: list[Chunk] = []
    for hpath, anchor, first, stop in sections:
        blocks = _blocks(lines, first, stop, sc, regions)
        for n, (a, b, kind, reg, body) in enumerate(_pack(lines, blocks, max_chars, overlap)):
            r = regions[reg] if reg >= 0 else None
            digest = hasher(body.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    path,
                    f"{path}#{anchor}~{n}",
                    hpath,
                    a + 1,
                    b,
                    body,
                    digest,
                    kind,
                    f"{r.doc}/{r.name}" if r else "",
                    "generated" if r else "human",
                    doctype,
                )
            )
    return Chunked(tuple(chunks), meta)
