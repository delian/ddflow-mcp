"""What ddflow costs a session before its first turn is counted, shown by `ddflow doctor`
and ratcheted (B-doctor-no-session-start-cost): the MCP instructions, `tools/list` for the
active profile and the session brief were measured one at a time, so a 30K-token start
was invisible."""

from __future__ import annotations

import json
import re

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.services import startcost as SC
from ddflow.surfaces import mcp
from ddflow.surfaces.startcost_texts import start_texts

#: Tokens ddflow itself spends on a fresh adopted project at the default `[mcp].tools = all`
#: (chars / 4). Raise only with a reason in the commit; lowering is welcome. Measured 2026-10-22:
#: instructions ~3.1K, tools/list ~26K, brief ~1.4K.
START_COST_CEILING = 33_000


def _adopted(repo):
    run_cli(repo, "init")
    return repo


def test_doctor_shows_the_session_start_cost(repo):
    _adopted(repo)
    _code, out, err = run_cli(repo, "doctor")
    line = next((x for x in out.splitlines() if "session start cost:" in x), "")
    assert re.search(r"session start cost: \d+ tokens \(budget \d+\)", line), out + err


def test_doctor_over_the_mcp_tool_shows_the_same_line(repo):
    _adopted(repo)
    reply = mcp.Server(repo, called_from=repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_doctor", "arguments": {}},
        }
    )
    assert "session start cost:" in reply["result"]["content"][0]["text"]


def test_the_json_report_carries_the_numbers(repo):
    _adopted(repo)
    _, out, _ = run_cli(repo, "--json", "doctor")
    assert json.loads(out)["notes"], out
    assert any("session start cost:" in n for n in json.loads(out)["notes"])


def test_a_cost_past_the_budget_says_over():
    cost = SC.measure({"a": "x" * 400}, chars_per_token=4.0, budget=50)
    assert (cost.tokens, cost.over) == (100, 50)
    assert cost.line().startswith("session start cost: 100 tokens (budget 50) -- OVER by 50")
    assert SC.measure({"a": "x" * 400}, chars_per_token=4.0, budget=0).over == 0


def test_the_ratio_is_the_knobs_not_a_constant():
    a = SC.measure({"a": "x" * 400}, chars_per_token=4.0, budget=0).tokens
    b = SC.measure({"a": "x" * 400}, chars_per_token=2.0, budget=0).tokens
    assert (a, b) == (100, 200)


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True])
def test_the_ratio_knob_refuses_what_cannot_divide(bad):
    from ddflow.config import _KNOB_CHECKS

    assert _KNOB_CHECKS["session.start_chars_per_token"](bad)
    assert not _KNOB_CHECKS["session.start_chars_per_token"](4)


def test_the_start_cost_stays_under_its_ceiling(repo):
    _adopted(repo)
    cfg = Config.load(repo)
    cost = SC.measure(
        start_texts(repo, "x"),
        chars_per_token=cfg.session.start_chars_per_token,
        budget=cfg.session.context_budget_tokens,
    )
    assert all(cost.parts.values()), cost.parts  # a part that measured 0 is a part that broke
    assert cost.tokens <= START_COST_CEILING, cost.line()
    assert cost.over == 0, cost.line()


def test_measuring_leaves_the_once_per_version_notice_alone(repo):
    """Counting the handshake and the brief must not use up the upgrade notice or sweep."""
    from ddflow.api.lifecycle.brief import _upgrade_notices

    _adopted(repo)
    with SC.measuring():
        assert _upgrade_notices(repo, None, None, None, "x") == ""
        assert mcp._upgrade_line(repo, "x") == ""
    assert not SC.is_measuring()


def test_the_cost_is_counted_in_bytes_not_code_points():
    cost = SC.measure({"a": "é" * 400}, chars_per_token=4.0, budget=0)
    assert (cost.chars["a"], cost.tokens) == (800, 200)


def test_report_builds_the_texts_while_measuring(repo):
    seen = []

    def texts(_repo, _agent):
        seen.append(SC.is_measuring())
        return {"a": "x" * 40}

    cost = SC.report(repo, Config.load(repo), "x", texts)
    assert seen == [True] and cost.tokens == 10


def test_a_failing_surface_leaves_an_empty_cost_not_a_failed_doctor(repo):
    def boom(_repo, _agent):
        raise RuntimeError("no texts")

    cost = SC.report(repo, Config.load(repo), "x", boom)
    assert cost.notes() == [] and cost.data() == {}


def test_the_knob_says_what_is_and_is_not_counted():
    from ddflow.config import KNOB_DOCS

    doc = KNOB_DOCS["session.context_budget_tokens"]
    assert "SessionStart hook" in doc and "upgrade notice is not counted" in doc
