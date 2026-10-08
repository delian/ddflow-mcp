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


def test_the_recall_default_is_defined_once():
    """It was 4000 in the API, the CLI parser, the MCP tool and the MCP bound."""
    from ddflow.api.knowledge import retrieval
    from ddflow.surfaces import mcp_bound
    from ddflow.surfaces.parsers import knowledge as parser

    assert inspect.signature(retrieval.recall).parameters["max_chars"].default == B.RECALL_MAX_CHARS
    assert mcp_bound.RECALL_BUDGET == B.RECALL_MAX_CHARS
    src = inspect.getsource(parser)
    assert "default=RECALL_MAX_CHARS" in src and "default=4000" not in src
