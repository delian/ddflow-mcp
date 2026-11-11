"""The review contract doc: held honest the same way the realmreview engine is.

The contract names one checker (the engine the launch brief points every subagent at), so the
doc cannot promise a mechanism that does not exist, and the mechanism is the one the test
exercises: a round is parsed into findings with a stable id, and an off-contract reply is
flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.services.review.engine import parse
from ddflow.services.review.model import finding_digest

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "ddflow" / "drivers" / "review-contract.md"


def test_the_contract_ships():
    assert CONTRACT.is_file(), "the review contract must ship"


def test_the_contract_states_the_stop_rule():
    text = CONTRACT.read_text()
    assert str(4) in text and "max_rounds" in text, "the stop rule must name round 4"
    for term in ("round", "finding", "refuted", "confirmed", "off contract"):
        assert re.search(term, text), f"the contract does not state the {term!r} rule"


def test_a_repeated_closed_finding_is_the_same_id_as_the_one_it_repeats():
    """A finding's id is its full text: a round that repeats a closed finding word-for-word
    yields the same id, so the repeat is seen as the closed one, not a new one to re-litigate."""
    first = parse(
        "FINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input\nSTATUS: FINDINGS 1\n"
    )[0]
    repeat = parse(
        "STATUS: FINDINGS 1\nFINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input\n"
    )[0]
    assert repeat
    assert finding_digest(repeat[0].severity, repeat[0].location, repeat[0].title, repeat[0].detail) == (
        finding_digest(first[0].severity, first[0].location, first[0].title, first[0].detail)
    ), "a repeated finding did not produce the same id as the closed one it repeats"


def test_an_off_contract_reply_is_flagged():
    """A round with no STATUS: block is off contract, never a clean review."""
    findings, on_contract = parse("FINDING MEDIUM ddflow/core/nope.py:2\nno status here\n")
    assert findings and not on_contract, "a reply with no STATUS: block was not flagged off contract"
