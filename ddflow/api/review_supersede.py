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
    ranges = next(
        (r for p, r in changed.items() if p == m.group(1) or p.endswith("/" + m.group(1))), None
    )
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
