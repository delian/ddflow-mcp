"""`ddflow harness parity`: the outcome matrix by agent and feature, both directions.

One view over the parity answers (``services.parity.answers``) and the feature catalogue
(``services.parity.catalog``): per agent the status of every in-agent and orchestrator
feature, per-axis totals (verified / documented / unverified / none), and the first
unverified row that blocks the next tier. Folds the existing ``ddflow harness show`` facts,
it does not duplicate them; ``--json`` returns the same rows for tooling.
"""

from __future__ import annotations

from collections import Counter

from ..services import harnessreg as HR
from ..services.parity import answers as AN
from ..services.parity import catalog as CAT


def rows(hs: list | None = None) -> dict[str, dict[str, str]]:
    """``{agent id: {feature: status}}`` for every adopted-feature row (no history rows)."""
    hs = list(HR.load() if hs is None else hs)
    return {h.id: AN.answers(h) for h in hs}


def totals(answer: dict[str, str], axis_rows: list) -> dict[str, int]:
    """Per-status count over the ``axis`` catalogue rows of ``answer``."""
    counts = Counter(answer[r.feature] for r in axis_rows)
    return {s: counts[s] for s in ("verified", "documented", "unverified", "none")}


def blocking(answer: dict[str, str], axis_rows: list) -> str:
    """The first unverified row that has no rung yet (blocks the next tier), or ``""``."""
    return next((r.feature for r in axis_rows if answer[r.feature] == "unverified"), "")


def render(hs: list | None = None) -> str:
    """The matrix text: one line per agent+axis of totals, then the first blocking row."""
    hs = list(HR.load() if hs is None else hs)
    catalog = [r for r in CAT.load().rows if r.axis in ("in_agent", "orchestrator")]
    out: list[str] = []
    for h in hs:
        answer = AN.answers(h)
        for axis in ("in_agent", "orchestrator"):
            axis_rows = [r for r in catalog if r.axis == axis]
            t = totals(answer, axis_rows)
            first = blocking(answer, axis_rows)
            out.append(
                f"{h.id} {axis:12} verified={t['verified']} documented={t['documented']} "
                f"unverified={t['unverified']} none={t['none']} first_blocking={first}"
            )
    return "\n".join(out)


def harness_parity(hs: list | None = None, as_json: bool = False) -> str:
    """The CLI/MCP entry: the matrix rows, or one JSON object for tooling."""
    hs = list(HR.load() if hs is None else hs)
    if as_json:
        import json

        return json.dumps(rows(hs), sort_keys=True)
    return render(hs)
