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

_MARK = "ddflow:begin"
# adopt's stamped blocks (`ddflow:begin NAME ddflow=V fmt=N sha=H`) are another grammar, owned
# by pins and adopt: a text holding one is not read here, so its end marker is not "unbalanced"
_STAMPED = re.compile(r"^<!-- ddflow:begin \S+ [^\n]*\bfmt=\d+", re.M)


def _parsed(text: str) -> list[RG.Region]:
    return RG.parse(text) if _MARK in text and not _STAMPED.search(text) else []


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
