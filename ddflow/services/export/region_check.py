"""Read-only questions about the named regions of one document text (B-tpl-refresh).

``edited(text)`` names the regions whose body no longer matches the digest in their begin
marker (the pre-commit check and the doctor note); ``mask(text)`` blanks generated region
bodies, keeping line numbers, so prose checks never judge text a template wrote.
A text whose markers cannot be read (unbalanced, duplicate) is reported by ``problem``
and left unmasked: "could not tell" is never read as clean or as generated.
"""

from __future__ import annotations

import re

from . import regions as RG

_MARKS = ("ddflow:begin", "ddflow:end")  # an orphaned end marker is unbalanced, not "no regions"
# adopt's stamped blocks (`ddflow:begin NAME ddflow=V fmt=N sha=H` ... `ddflow:end NAME`) are
# another grammar, owned by pins and adopt: each block is blanked (offsets kept) before
# parsing, so its end marker is not "unbalanced" and the real regions around it are still read
_STAMPED = re.compile(r"^<!-- ddflow:begin (\S+) [^\n]*\bfmt=\d+[^\n]*$", re.M)


def _blank(text: str) -> str:
    """``text`` with each stamped block turned to spaces (newlines and offsets kept)."""
    out, cur = [], 0
    for m in _STAMPED.finditer(text):
        if m.start() < cur:
            continue
        end = text.find(f"<!-- ddflow:end {m.group(1)} -->", m.end())
        stop = m.end() if end < 0 else (text.find("\n", end) + 1 or len(text))
        out.append(text[cur : m.start()] + re.sub(r"[^\n]", " ", text[m.start() : stop]))
        cur = stop
    return "".join(out) + text[cur:]


def _parsed(text: str) -> list[RG.Region]:
    if not any(m in text for m in _MARKS):
        return []
    return RG.parse(_blank(text) if _STAMPED.search(text) else text)


def problem(text: str) -> str:
    """Why the markers of ``text`` cannot be read, or ``""``."""
    try:
        _parsed(text)
    except RG.RegionError as exc:
        return str(exc)
    return ""


def edited(text: str) -> list[str]:
    """``doc/name`` of each region hand-edited since ddflow wrote it, in file order."""
    try:
        return [f"{r.doc}/{r.name}" for r in _parsed(text) if r.edited]
    except RG.RegionError:
        return []  # reported by `problem`, not here


def mask(text: str) -> str:
    """``text`` with every region body replaced by blank lines (same line count)."""
    try:
        regions = _parsed(text)
    except RG.RegionError:
        return text
    out: list[str] = []
    cur = 0
    for r in regions:
        out.append(text[cur : r.body])
        out.append("\n" * r.content.count("\n"))
        cur = r.end
    out.append(text[cur:])
    return "".join(out)
