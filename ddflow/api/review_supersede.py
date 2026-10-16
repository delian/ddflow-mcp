"""Which prior review findings a later commit already rewrote (bug Be1818cd552).

A finding cites `path:line`; when the commits since the reviewed head rewrote those
lines the finding describes code that no longer exists. It is `superseded`: a re-review
is told not to re-report it, and it does not hold a clean round (`_merge_delta`)."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_FILE = re.compile(r"^--- (?:a/)?(.+?)\s*$")
_LOC = re.compile(r"([\w./+@-]+\.\w+)(?::(\d+)(?:\s*[-–]\s*(\d+))?)?")


def changed_old_lines(diff: str) -> dict[str, list[tuple[int, int]]]:
    """path -> [(first, last)] OLD-side line ranges ``diff`` removed or replaced (context
    lines are not changes; a pure insertion rewrites no existing line, so marks none)."""
    out: dict[str, list[tuple[int, int]]] = {}
    path = ""
    pos = 0
    for line in diff.splitlines():
        m = _FILE.match(line)
        if m and not line.startswith("--- /dev/null"):
            path = m.group(1)
            out.setdefault(path, [])
            pos = 0
            continue
        h = _HUNK.match(line)
        if h:
            pos = int(h.group(1))
        elif pos and path and line.startswith("-"):
            out[path].append((pos, pos))
            pos += 1
        elif pos and path and line.startswith(" "):
            pos += 1
    return out


def is_superseded(location: str, changed: dict[str, list[tuple[int, int]]]) -> bool:
    """Does ``location`` (`path:line`, `path:a-b` or a bare path) sit on lines ``changed``
    rewrote? A bare path counts when the file has a change; no path, never."""
    m = _LOC.search(location or "")
    if not m:
        return False
    ranges = next((r for p, r in changed.items() if p == m.group(1) or p.endswith("/" + m.group(1))), None)
    if not ranges:
        return False
    if m.group(2) is None:
        return True
    lo = int(m.group(2))
    hi = int(m.group(3) or lo)
    return any(a <= hi and lo <= b for a, b in ranges)


def superseded_digests(found: list[dict], diff: str) -> set[str]:
    """Digests of the findings in ``found`` whose cited lines ``diff`` changed."""
    changed = changed_old_lines(diff)
    return {
        str(f.get("digest"))
        for f in found
        if f.get("digest") and is_superseded(str(f.get("location") or ""), changed)
    }
