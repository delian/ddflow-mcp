"""`ddflow harness parity`: the outcome matrix by agent and feature, both directions.

One view over the parity answers (``services.parity.answers``) and the feature catalogue
(``services.parity.catalog``): the feature matrix (per agent the status of every in-agent and
orchestrator feature), per-axis totals (verified / documented / unverified / none), and the
first unverified row that blocks the next tier. Folds the existing ``ddflow harness show``
facts, it does not duplicate them; ``--json`` returns the full report for tooling.
"""

from __future__ import annotations

import json
from collections import Counter

from ..services import facts
from ..services import harnessreg as HR
from ..services.parity import answers as AN
from ..services.parity import catalog as CAT

_STATES = facts.STATUSES


def active_rows() -> list:
    return [r for r in CAT.load().rows if r.axis in ("in_agent", "orchestrator")]


def rows(hs: list | None = None) -> dict[str, dict[str, str]]:
    """``{agent id: {feature: status}}`` for every adopted feature (no history rows)."""
    hs = list(HR.load() if hs is None else hs)
    return {h.id: AN.answers(h) for h in hs}


def totals(answer: dict[str, str], axis_rows: list) -> dict[str, int]:
    """Per-status count over the ``axis`` catalogue rows of ``answer``."""
    counts = Counter(answer[r.feature] for r in axis_rows)
    return {s: counts[s] for s in _STATES}


def blocking(answer: dict[str, str], axis_rows: list) -> str:
    """The first unverified row of the axis (no rung yet; blocks the next tier), or ``""``."""
    return next((r.feature for r in axis_rows if answer[r.feature] == "unverified"), "")


def summary(hs: list | None = None) -> dict[str, dict]:
    """Per agent: per-axis totals and the blocking row, beside the matrix's answers."""
    hs = list(HR.load() if hs is None else hs)
    catalog = active_rows()
    out = {}
    for h in hs:
        answer = AN.answers(h)
        out[h.id] = {
            axis: {
                "totals": totals(answer, [r for r in catalog if r.axis == axis]),
                "first_blocking": blocking(answer, [r for r in catalog if r.axis == axis]),
            }
            for axis in ("in_agent", "orchestrator")
        }
    return out


def render(hs: list | None = None) -> str:
    """The text matrix: one agent line per axis with its status counts, then the first
    blocking row of that axis."""
    hs = list(HR.load() if hs is None else hs)
    catalog = active_rows()
    out: list[str] = []
    for h in hs:
        answer = AN.answers(h)
        for axis in ("in_agent", "orchestrator"):
            axis_rows = [r for r in catalog if r.axis == axis]
            t = totals(answer, axis_rows)
            first = blocking(answer, axis_rows)
            cells = " ".join(f"{r.feature}={answer[r.feature]}" for r in axis_rows)
            out.append(
                f"{h.id} {axis:12} {cells} "
                f"verified={t['verified']} documented={t['documented']} "
                f"unverified={t['unverified']} none={t['none']} first_blocking={first}"
            )
    return "\n".join(out)


def harness_parity(hs: list | None = None, as_json: bool = False) -> str:
    """The CLI/MCP entry: the full report (matrix + per-agent totals + first blocking row)."""
    hs = list(HR.load() if hs is None else hs)
    if as_json:
        return json.dumps({"matrix": rows(hs), "summary": summary(hs)}, sort_keys=True)
    return render(hs)
