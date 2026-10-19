"""The 8-line form of the completion report: `ddflow report ITEM --brief`.

An agent's final message used to paste the whole report (60-120 lines) on top of data the
queue already holds. These lines carry what the coordinator acts on: the landing sha, the
regression test, the forced or skipped gates, the ids it filed and what is left. The full
report stays one command away.
"""

from __future__ import annotations

from typing import Any


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: max(chars - 1, 0)] + "…"


def _ids(rows: list[dict[str, Any]]) -> str:
    return ", ".join(f"{r['id']}{'' if r.get('open', True) else ' (closed)'}" for r in rows)


def _landed(ld: dict[str, Any]) -> str:
    sha = (ld.get("merge_sha") or "")[:12]
    return f"landed: {sha} on {ld['base'] or 'the base'}" if sha else "landed: not merged"


def _regression(reg: dict[str, Any]) -> str:
    rows = [
        f"{b['bug']}: {', '.join(b['tests']) or 'no test'} ({b['verified'] or 'unverified'})"
        for b in reg["bugs"]
    ]
    files = ["test files: " + ", ".join(reg["test_files"][:3])] if reg["test_files"] else []
    return "regression: " + ("; ".join(rows or files) or "none")


def _filed(filed: dict[str, Any]) -> str:
    ids = [i for i in (_ids(filed["bugs"]), _ids(filed["tasks"])) if i]
    notes = ", ".join(n["id"] for n in filed.get("notes", []))
    return f"filed: {', '.join(ids) or 'none'}" + (f"; notes {notes}" if notes else "")


def _list(label: str, items: list[str], sep: str) -> str:
    return f"{label}: {sep.join(items) or 'none'}"


def lines(d: dict[str, Any]) -> list[str]:
    """The 8 lines of a collected report (`services.report.collect`)."""
    it = d["item"]
    off = [f"{g['gate']} {g['outcome']}" for g in d["gates"] if g["outcome"] != "passed"]
    return [
        f"{it['id']}: {it['state']} ({it['agent']})",
        _landed(d["landing"]),
        _regression(d["regression"]),
        _list("gates not passed", off, ", "),
        _list("forced or skipped", d["overrides"], "; "),
        _filed(d["filed"]),
        _list("follow-ups", d["follow_ups"], ", "),
        _list("not done", d["not_done"], "; "),
    ]


def render(d: dict[str, Any], chars: int) -> str:
    """The brief as text. Only the free-text entries (each override and each not-done note) are
    cut, to ``chars`` ([session].report_brief_chars); no id, gate name, test or sha is ever cut:
    a cut id names nothing, or the wrong item."""
    d = {
        **d,
        "overrides": [_cut(t, chars) for t in d["overrides"]],
        "not_done": [_cut(t, chars) for t in d["not_done"]],
    }
    return "\n".join(lines(d)) + "\n"
