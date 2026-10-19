"""The 8-line form of the completion report: `ddflow report ITEM --brief`.

An agent's final message used to paste the whole report (60-120 lines) on top of data the
queue already holds. These lines carry what the coordinator acts on: the landing sha, the
regression test, the forced or skipped gates, the ids it filed and what is left. The full
report stays one command away.
"""

from __future__ import annotations

from typing import Any

#: The brief is always these lines, in this order; a line with nothing to say reads ``none``.
LINES = 8


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: max(chars - 1, 0)] + "…"


def _ids(rows: list[dict[str, Any]]) -> str:
    return ", ".join(f"{r['id']}{'' if r.get('open', True) else ' (closed)'}" for r in rows)


def lines(d: dict[str, Any]) -> list[str]:
    """The ``LINES`` lines of a collected report (`services.report.collect`)."""
    it, ld = d["item"], d["landing"]
    sha = (ld.get("merge_sha") or "")[:12]
    bugs = d["regression"]["bugs"]
    tests = "; ".join(
        f"{b['bug']}: {', '.join(b['tests']) or 'no test'} ({b['verified'] or 'unverified'})"
        for b in bugs
    ) or (
        "test files: " + ", ".join(d["regression"]["test_files"][:3])
        if d["regression"]["test_files"]
        else ""
    )
    off = [f"{g['gate']} {g['outcome']}" for g in d["gates"] if g["outcome"] != "passed"]
    filed = d["filed"]
    ids = (
        _ids(filed["bugs"])
        + (", " if filed["bugs"] and filed["tasks"] else "")
        + _ids(filed["tasks"])
    )
    notes = ", ".join(n["id"] for n in filed.get("notes", []))
    return [
        f"{it['id']}: {it['state']} ({it['agent']})",
        f"landed: {sha + ' on ' + (ld['base'] or 'the base') if sha else 'not merged'}",
        f"regression: {tests or 'none'}",
        f"gates not passed: {', '.join(off) or 'none'}",
        f"forced or skipped: {'; '.join(d['overrides']) or 'none'}",
        f"filed: {ids or 'none'}" + (f"; notes {notes}" if notes else ""),
        f"follow-ups: {', '.join(d['follow_ups']) or 'none'}",
        f"not done: {'; '.join(d['not_done']) or 'none'}",
    ]


#: The lines (0-based) that hold ids: never cut, a cut id names nothing or the wrong item.
_WHOLE = (0, 1, 5, 6)


def render(d: dict[str, Any], chars: int) -> str:
    """The brief as text: the free-text lines cut to ``chars`` ([session].report_brief_chars);
    the lines of ids and the landing sha are kept whole."""
    return "\n".join(t if i in _WHOLE else _cut(t, chars) for i, t in enumerate(lines(d))) + "\n"
