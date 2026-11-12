"""The pull-request description, rendered from a layered template (BL-pr-evidence-template).

`[flow].pr_body_template` names a template file; empty means `.ddflow/prompts/pr_body.md` if the
project has one, else the shipped `templates/prompts/pr_body.md` (the same overlay every prompt
uses, so a company layer is a different shipped/project file). Every placeholder is DERIVED from
records already in the log -- the item, its gates, the review triage, the bugs it fixed with their
regression tests, the changelog entry -- never from a live call, and the finished text passes the
secret redactor before it can leave the project (r-consent-outward): the request is outward text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, State
from .. import prompts as P
from ..redact_report import redact_secrets

TEMPLATE = "pr_body"


def _first_line(text: object) -> str:
    return (str(text or "").strip().splitlines() or [""])[0]


def _triage_counts(it: Item, gate: str) -> dict[str, int]:
    out = {"confirmed": 0, "refuted": 0}
    for row in (it.triage.get(gate) or {}).values():
        verdict = str((row or {}).get("verdict", ""))
        if verdict in out:
            out[verdict] += 1
    return out


def _gates(it: Item) -> list[dict[str, Any]]:
    rows = []
    for gate, rec in it.gates.items():
        if not rec.outcome:
            continue
        ev = rec.evidence or {}
        rows.append(
            {
                "gate": gate,
                "outcome": rec.outcome,
                "note": _first_line(rec.reason),
                "rounds": int(ev.get("rounds") or 0),
                **{f"triage_{k}": v for k, v in _triage_counts(it, gate).items()},
            }
        )
    return rows


def _bugs(st: State, it: Item) -> list[dict[str, Any]]:
    return [
        {
            "id": b.id,
            "summary": _first_line(b.summary),
            "regression_tests": list(b.regression_tests or ([b.regression_test] if b.regression_test else [])),
        }
        for b in st.bugs.values()
        if b.item == it.id
    ]


def variables(st: State, it: Item, on: Item | None) -> dict[str, Any]:
    """Every placeholder the template can use, from derived records only."""
    return {
        "item": it.id,
        "title": it.title,
        "body": it.body.strip() or it.title,
        "stacked_on": (on.id if on is not None else ""),
        "stacked_on_url": (on.pr.url if on is not None and on.pr else ""),
        "gates": _gates(it),
        "bugs": _bugs(st, it),
        "changelog": dict(it.changelog or {}),
    }


def render_body(
    st: State, it: Item, *, on: Item | None, cfg: Config, repo: Path | None = None
) -> str:
    """The request body: template (config path > project > shipped), then redacted."""
    override = {TEMPLATE: cfg.flow.pr_body_template} if cfg.flow.pr_body_template else {}
    text = P.render(P.resolve(TEMPLATE, repo, override), **variables(st, it, on)).strip()
    clean, _n = redact_secrets(text, cfg)
    return clean
