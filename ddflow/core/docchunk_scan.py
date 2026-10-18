"""Where the headings, fences and tables of a document are: Markdown, reStructuredText, AsciiDoc.

`docchunk` cuts chunks from what these scanners find. Each scanner reads the lines once and
returns a `Scan`: headings ``(line, level, title)`` and the line spans of code blocks and
tables (0-based, inclusive). Pure, stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_RST_ADORN = re.compile(r"^([!-/:-@\[-`{-~])\1+\s*$")
_ADOC_HEAD = re.compile(r"^(={1,6})[ \t]+(\S.*?)\s*$")
_ADOC_BLOCK = re.compile(r"^(-{4,}|\.{4,})\s*$")


def front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
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
class Scan:
    heads: list[tuple[int, int, str]] = field(default_factory=list)  # line, level, title
    code: list[tuple[int, int]] = field(default_factory=list)  # [first, last] line indexes
    table: list[tuple[int, int]] = field(default_factory=list)


def _scan_md(lines: list[str], start: int) -> Scan:
    sc, i, n = Scan(), start, len(lines)
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


def _scan_rst(lines: list[str], start: int) -> Scan:
    sc, styles, i, n = Scan(), [], start, len(lines)
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


def _scan_adoc(lines: list[str], start: int) -> Scan:
    sc, i, n = Scan(), start, len(lines)
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


SCANNERS = {"md": _scan_md, "rst": _scan_rst, "adoc": _scan_adoc}
