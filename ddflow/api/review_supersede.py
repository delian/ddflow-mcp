"""Which prior review findings a later commit already rewrote (bug Be1818cd552).

A finding cites `path:line`; when the commits since the reviewed head rewrote those
lines the finding describes code that no longer exists. It is `superseded`: a re-review
is told not to re-report it, and it does not hold a clean round (`_merge_delta`)."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_FILE = re.compile(r"^--- (?:a/)?(.+?)\s*$")
_LOC = re.compile(r"([\w./+@-]+):(\d+)(?:\s*[-\u2013]\s*(\d+))?")


def changed_old_lines(diff: str) -> dict[str, list[tuple[int, int]]]:
    """path -> [(first, last)] OLD-side line ranges ``diff`` removed or replaced (context
    lines are not changes; a pure insertion rewrites no existing line, so marks none)."""
    out: dict[str, list[tuple[int, int]]] = {}
    path = ""
    pos = old_left = new_left = 0
    rows = diff.splitlines()
    for i, line in enumerate(rows):
        if old_left or new_left:  # inside a hunk: `-- x` / `++ y` are content, not headers
            if line.startswith("-"):
                out[path].append((pos, pos))
                pos += 1
                old_left -= 1
            elif line.startswith("+"):
                new_left -= 1
            elif line.startswith(" "):
                pos += 1
                old_left -= 1
                new_left -= 1
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else ""
        if line.startswith("--- ") and nxt.startswith("+++ "):
            m = _FILE.match(line)
            path = "" if line.startswith("--- /dev/null") or not m else m.group(1)
            if path:
                out.setdefault(path, [])
            continue
        h = _HUNK.match(line)
        if h and path:
            pos = int(h.group(1))
            old_left = int(h.group(2) if h.group(2) is not None else 1)
            new_left = int(h.group(3) if h.group(3) is not None else 1)
    return out


def is_superseded(location: str, changed: dict[str, list[tuple[int, int]]]) -> bool:
    """Does ``location`` (`path:line` or `path:a-b`) sit on lines ``changed`` rewrote? A
    bare path or a symbol names no line, so it never counts: that finding is re-checked."""
    m = _LOC.search(location or "")
    if not m:
        return False
    want = m.group(1)
    ranges = changed.get(want)
    if ranges is None:  # a citation may drop the leading directories
        ranges = next((r for p, r in changed.items() if p.endswith("/" + want)), None)
    if not ranges:
        return False
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


def carry(ev: dict, prior: dict) -> set[str]:
    """Fold the superseded digests of the record being replaced into ``ev``; returns all."""
    gone = set(ev.get("superseded") or []) | set(prior.get("superseded") or [])
    if gone:
        ev["superseded"] = sorted(gone)
    return gone


def round_evidence(
    extra: dict | None, supers: set[str], fix: bool, deltas: int, say
) -> dict | None:
    """``extra`` plus what a fix round / superseded findings add to the round's evidence;
    a fix round keeps the delta count where it was (it is not counted)."""
    if fix:
        say(
            "fix round: the commits since the reviewed head rewrote or confirmed every prior finding; not counted against max_rounds."
        )
        extra = {**(extra or {}), "fix_round": True, "delta_rounds": deltas}
    if supers:
        extra = {**(extra or {}), "superseded": sorted(supers)}
    return extra
