"""The one fact record (HX-facts-core): `{v, s, src, d, ver?, note?}`.

Shared by the context tables (`context_families`) and the descriptor `[facts]` overlay
(`harnessfacts`); DRV-facts extends it with a version range and expiry. Three-valued on
purpose: `s` is `verified` or `documented` (believable), `unverified` (a lead, never read
as true) or `none` (a documented absence). `v` is required of a readable fact in a context table (`context_families` checks it) and
absent from a descriptor overlay, whose value is the descriptor's own, so `problems` does
not require it. `ver` is the tool version the fact was read
against; deciding it went stale is `P-agent-parity.drift`, not this module.
"""

from __future__ import annotations

from typing import Any

from ..core import clock

#: `v` value, `s` status, `src` source id, `d` ISO date, `ver` tool version, `note`;
#: `exc` / `only` scope a context fact to models (see context_families).
FACT_KEYS = ("v", "s", "src", "d", "ver", "note", "exc", "only")
STATUSES = ("verified", "documented", "unverified", "none")
READABLE = ("verified", "documented")


def is_fact(node: Any) -> bool:
    return isinstance(node, dict) and "s" in node


def problems(at: str, fact: dict[str, Any], sources: Any = None) -> list[str]:
    """Defects of one fact record, one string each. `sources`, when given, is the table
    the source id must be in; otherwise any non-empty string will do."""
    out = []
    extra = sorted(set(fact) - set(FACT_KEYS))
    if extra:
        out.append(f"{at} has unknown field(s) {extra}")
    if fact.get("s") not in STATUSES:
        out.append(f"{at}: status {fact.get('s')!r} is not one of {STATUSES}")
    src = fact.get("src")
    if not isinstance(src, str) or not src or (sources is not None and src not in sources):
        out.append(f"{at}: source {src!r} is missing or not in [sources]")
    try:
        clock.parse_date(str(fact.get("d")))
    except ValueError:
        out.append(f"{at}: date {fact.get('d')!r} is not ISO")
    if "ver" in fact and not isinstance(fact["ver"], str):
        out.append(f"{at}: ver must be a string")
    return out
