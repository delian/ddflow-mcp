"""The shared budget vocabulary (core/budget.py) and, later, the context pack."""

from __future__ import annotations

import inspect

from ddflow.core import budget as B


def test_approx_tokens_is_the_estimate_the_brief_always_used():
    for text in ("", "a", "abcd", "x" * 399, "x" * 400, "é" * 41):
        assert B.approx_tokens(text) == max(1, len(text) // 4)
    assert B.chars_for(0) == 0 and B.chars_for(250) == 1000


def test_budget_states_its_unit():
    t = B.Budget(100, "tokens")
    assert (t.chars, t.tokens) == (400, 100)
    assert t.cost("x" * 40) == 10 and t.fits("x" * 400) and not t.fits("x" * 404)
    c = B.Budget(4000)
    assert (c.chars, c.tokens) == (4000, 1000)
    assert c.cost("x" * 10) == 10 and c.fits("x" * 4000) and not c.fits("x" * 4001)
    assert B.Budget(2, "chars").tokens == 1  # never zero: a budget of nothing is not a budget


def test_the_recall_default_is_4000_everywhere(monkeypatch):
    """It was 4000 in the API, the CLI parser, the MCP tool and the MCP bound; one constant
    now serves all four, and the value is pinned here so a change of it is deliberate."""
    from ddflow.api.knowledge import retrieval
    from ddflow.surfaces import mcp_bound
    from ddflow.surfaces.parsers import knowledge as parser
    from ddflow.surfaces.tools import reporting

    assert B.RECALL_MAX_CHARS == 4000
    assert inspect.signature(retrieval.recall).parameters["max_chars"].default == 4000
    assert mcp_bound.RECALL_BUDGET == 4000
    assert "default=RECALL_MAX_CHARS" in inspect.getsource(parser)

    seen = {}

    class _Api:
        def recall(self, repo, query, **kw):
            seen.update(kw)

    monkeypatch.setattr(reporting, "_api", _Api)
    reporting.TOOLS["ddflow_recall"]["api"](None, {"query": "q"}, "")
    assert seen["max_chars"] == 4000
    reporting.TOOLS["ddflow_recall"]["api"](None, {"query": "q", "max_chars": 900}, "")
    assert seen["max_chars"] == 900


def test_the_brief_of_an_empty_project_reports_at_least_the_clamp(repo):
    """`approx_tokens` clamps at 1 where the old `len(text) // 4` could say 0; a brief always
    opens with its heading, so the two agree on the emptiest brief there is."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "--json", "brief")
    body = json.loads(out)
    assert code == 0 and len(body["brief"]) >= 4  # the clamp and the plain // 4 coincide
    assert body["approx_tokens"] == len(body["brief"]) // 4 >= 1
