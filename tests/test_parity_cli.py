"""The parity matrix CLI view (PAR-matrix-cli): per-agent feature answers, axis totals and
the first blocking row, folded from the parity answers (never re-derived). Golden-output
pinned so a regressed status or a lost row breaks here.
"""

from __future__ import annotations

import json

from ddflow.api import harness_parity as P
from ddflow.services import harnessreg as HR
from ddflow.services.parity import catalog as CAT


def test_matrix_covers_every_adopted_agent_and_both_axes():
    matrix = P.rows()
    catalog = [r for r in CAT.load().rows if r.axis in ("in_agent", "orchestrator")]
    for h in HR.load():
        assert set(matrix[h.id]) == {r.feature for r in catalog}


def test_totals_sum_to_the_axis_row_count():
    catalog = [r for r in CAT.load().rows if r.axis in ("in_agent", "orchestrator")]
    for h in HR.load():
        answer = P.rows()[h.id]
        for axis in ("in_agent", "orchestrator"):
            axis_rows = [r for r in catalog if r.axis == axis]
            t = P.totals(answer, axis_rows)
            assert sum(t.values()) == len(axis_rows)
            assert set(t) == {"verified", "documented", "unverified", "none"}


def test_blocking_is_the_first_unverified_row():
    catalog = [r for r in CAT.load().rows if r.axis == "in_agent"]
    h = HR.get("grok")
    answer = P.rows()[h.id]
    blocked = P.blocking(answer, catalog)
    if blocked:
        assert answer[blocked] == "unverified"


def test_json_round_trips_per_agent_answers():
    payload = json.loads(P.harness_parity(as_json=True))
    matrix = P.rows()
    assert payload == {h.id: matrix[h.id] for h in HR.load()}


def test_render_one_line_per_agent_per_axis():
    text = P.render()
    lines = text.splitlines()
    assert len(lines) == len(HR.load()) * 2
    assert "first_blocking=" in lines[0]
