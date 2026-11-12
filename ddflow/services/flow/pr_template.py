"""The pull-request description, rendered from a layered template (BL-pr-evidence-template).

`[flow].pr_body_template` names a template file; empty means `.ddflow/prompts/pr_body.md` if the
project has one, else the shipped `templates/prompts/pr_body.md` (the same overlay every prompt
uses, so a company layer is a different shipped/project file). Every placeholder is DERIVED from
records already in the log -- the item, its gates, the review triage, the bugs it fixed with their
regression tests, the changelog entry -- never from a live call, and the finished text passes the
secret redactor before it can leave the project (r-consent-outward): the request is outward text.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, State
from .. import gates as G
from .. import prompts as P
from ..redact_report import redact_secrets

TEMPLATE = "pr_body"


def _first_line(text: object) -> str:
    return (str(text or "").strip().splitlines() or [""])[0]


def _cell(text: object, cell_max: int, scrub: Callable[[str], str]) -> str:
    """One Markdown table cell: SECRET-REDACTED first (on the raw text, so neither escaping nor a
    cut can hide a secret from the redactor or leave a prefix of one), then lines folded to
    spaces, backslashes escaped, the cell delimiter escaped, and the ESCAPED text bounded to
    ``cell_max`` (`[flow].pr_body_cell_max`)."""
    flat = scrub(
        " ".join(scrub(str(text or "")).split())
    )  # folding can CREATE a match: scrub again
    out = scrub(flat.replace("\\", "\\\\").replace("|", "\\|"))  # escaping can create a match too
    if len(out) > cell_max:
        cut = out[: cell_max - 1]
        if (len(cut) - len(cut.rstrip("\\"))) % 2:  # never end on a dangling escape
            cut = cut[:-1]
        out = cut + "\u2026"
    return out


def _triage_counts(it: Item, gate: str) -> dict[str, int]:
    out = {"confirmed": 0, "refuted": 0}
    for row in (it.triage.get(gate) or {}).values():
        verdict = str((row or {}).get("verdict", ""))
        if verdict in out:
            out[verdict] += 1
    return out


def _gates(
    it: Item, pipeline: list[str], cell_max: int, scrub: Callable[[str], str]
) -> list[dict[str, Any]]:
    """Every gate of the pipeline, in order, then any other recorded one; a gate with no
    recorded outcome says `not run` -- it is neither omitted nor shown as passed."""
    rows = []
    for gate in [*pipeline, *(g for g in it.gates if g not in pipeline)]:
        rec = it.gates.get(gate)
        ev = (rec.evidence if rec else None) or {}
        rows.append(
            {
                "gate": _cell(gate, cell_max, scrub),
                "outcome": _cell(
                    rec.outcome if rec and rec.outcome else "not run", cell_max, scrub
                ),
                "note": _cell(rec.reason if rec else "", cell_max, scrub),
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
            "regression_tests": ", ".join(
                b.regression_tests or ([b.regression_test] if b.regression_test else [])
            ),
        }
        for b in st.bugs.values()
        if b.item == it.id
    ]


def variables(
    st: State,
    it: Item,
    on: Item | None,
    pipeline: list[str],
    cell_max: int,
    scrub: Callable[[str], str],
) -> dict[str, Any]:
    """Every placeholder the template can use, from derived records only."""
    return {
        "item": it.id,
        "title": it.title,
        "body": it.body.strip() or it.title,
        "stacked_on": (on.id if on is not None else ""),
        "stacked_on_url": (on.pr.url if on is not None and on.pr else ""),
        "gates": _gates(it, pipeline, cell_max, scrub),
        "bugs": _bugs(st, it),
        "changelog": dict(it.changelog or {}),
    }


def render_body(
    st: State, it: Item, *, on: Item | None, cfg: Config, repo: Path | None = None
) -> str:
    """The request body: template (config path > project > shipped), then redacted."""
    override = {TEMPLATE: cfg.flow.pr_body_template} if cfg.flow.pr_body_template else {}
    text = P.render(
        P.resolve(TEMPLATE, repo, override),
        **variables(
            st,
            it,
            on,
            list(G.pipeline_for(it, cfg)),
            cfg.flow.pr_body_cell_max,
            lambda t: redact_secrets(t, cfg)[0],
        ),
    ).strip()
    clean, _n = redact_secrets(text, cfg)
    return clean
