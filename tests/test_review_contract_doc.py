"""The review contract doc: held honest the same way the review engine is.

The contract names one checker (the engine the launch brief points every subagent at), so the
doc cannot promise a mechanism that does not exist, and the mechanism is the one the test
exercises: a round is parsed into findings with a stable id, and a finding repeated
word-for-word is the same id as the closed one it repeats.
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


def test_a_repeated_closed_finding_shares_its_digest():
    """The contract says a finding is its full text: a round repeating a closed finding
    returns a finding with the same digest, so the repeat is visible instead of re-litigated."""
    closed = parse(
        "FINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input, which the caller dereferences\n"
        "STATUS: FINDINGS 1\n"
    )[0]
    assert closed
    repeat = parse(
        "STATUS: FINDINGS 1\n"
        "FINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input, which the caller dereferences\n"
    )[0]
    assert repeat
    assert {
        finding_digest(f.severity, f.location, f.title, f.detail) for f in repeat
    } == {
        finding_digest(f.severity, f.location, f.title, f.detail) for f in closed
    }, "a repeated finding did not produce the same digest as the closed one it repeats"


def test_an_off_contract_reply_is_flagged():
    """A round with no STATUS: block is off contract, never a clean review."""
    findings, on_contract = parse("FINDING MEDIUM ddflow/core/nope.py:2\nno status here\n")
    assert findings and not on_contract, "a reply with no STATUS: block was not flagged off contract"
