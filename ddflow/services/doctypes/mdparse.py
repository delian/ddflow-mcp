"""A document's structure, read with the standard library only (B-dt-validate).

``parse(text)`` returns a `Doc`: the YAML front matter (a safe subset: ``key: value`` scalars,
inline ``[a, b]`` lists, and ``- item`` block lists), the headings, and for each heading the
section it opens -- its body up to the next heading of the same or a higher level -- with the
code blocks, tables, list items and links in it. Fenced code is never read as prose or
structure. Pure: reads text, never the tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..docscheck_md import lines

_ATX = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)")
_ROW = re.compile(r"^.*\|.*$")
_DELIM = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_FENCE_RUN = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)")
_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_INT = re.compile(r"^-?\d+$")


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int


@dataclass
class Part:
    heading: Heading
    end: int  #: the last line of the section
    body: str = ""  #: its prose lines (code blocks left out), joined
    code_langs: list[str] = field(default_factory=list)  #: one entry per fenced block
    tables: int = 0
    items: list[str] = field(default_factory=list)  #: list item texts
    links: list[tuple[int, str]] = field(default_factory=list)  #: (line, target)

    @property
    def words(self) -> int:
        return len(self.body.split())


@dataclass
class Doc:
    front: dict[str, Any] = field(default_factory=dict)
    front_error: str = ""  #: why the front matter could not be read, or ""
    front_line: int = 0  #: the line after the front matter (1 when there is none)
    headings: list[Heading] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    whole: Part = field(
        default_factory=lambda: Part(Heading(0, "", 0), 0)
    )  #: the document as one part
    text: str = ""


def _scalar(raw: str) -> Any:
    v = raw.strip()
    if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~", ""):
        return None
    return int(v) if _INT.match(v) else v


def _flow(raw: str) -> Any:
    v = raw.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(p) for p in v[1:-1].split(",") if p.strip()]
    return _scalar(v)


def front_matter(text: str) -> tuple[dict[str, Any], str, int]:
    """(mapping, error, first line after the block). No block is ({}, "", 1)."""
    rows = text.splitlines()
    if not rows or rows[0].strip() != "---":
        return {}, "", 1
    close = next((i for i in range(1, len(rows)) if rows[i].strip() in ("---", "...")), 0)
    if not close:
        return {}, "front matter is opened with --- but never closed", 1
    out: dict[str, Any] = {}
    key = ""
    for n, row in enumerate(rows[1:close], 2):
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", row) or re.match(r"^-\s+(.*)$", row)
        if item and key and isinstance(out.get(key), list):
            out[key].append(_scalar(item.group(1)))
            continue
        m = _KEY.match(row)
        if not m:
            return out, f"line {n}: not a `key: value` pair in the front matter", close + 2
        key, val = m.group(1), m.group(2)
        out[key] = [] if val.strip() == "" else _flow(val)
    return out, "", close + 2


def parse(text: str) -> Doc:
    front, err, first = front_matter(text)
    doc = Doc(front, err, first, text=text)
    total = len(text.splitlines())
    doc.whole = Part(Heading(0, "", first - 1), total)
    live: list[Part] = [doc.whole]  # parts still open: each runs to a heading at or above it
    fence: tuple[str, int] | None = None  # the open code block's fence character and length
    prev = ""
    for n, line, code in lines(text):
        if n < first:
            continue
        if code:
            m = _FENCE_RUN.match(line)
            if fence is None:
                fence = (m.group(1)[0], len(m.group(1))) if m else ("`", 3)
                for s in live:
                    s.code_langs.append(m.group(2) if m else "")
            elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = fence if line.strip() != m.group(1) else None  # a closer has no info string
            prev = line
            continue
        fence = None
        if h := _ATX.match(line):
            head = Heading(len(h.group(1)), h.group(2), n)
            for s in [s for s in live if s.heading.level >= head.level]:
                s.end = n - 1
                live.remove(s)
            sec = Part(head, total)
            live.append(sec)
            doc.parts.append(sec)
            doc.headings.append(head)
        else:
            _read(live, line, prev, n)
        prev = line
    return doc


def _read(live: list[Part], line: str, prev: str, n: int) -> None:
    item = _ITEM.match(line)
    table = bool(_DELIM.match(line) and _ROW.match(prev))
    for s in live:
        s.body += ("\n" if s.body else "") + line
        s.tables += table
        if item:
            s.items.append(item.group(1))
        s.links += [(n, m.group(1)) for m in _LINK.finditer(line)]
