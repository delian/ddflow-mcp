"""The review contract doc: held honest the same way the review engine is.

The contract names one checker (the engine the launch brief points every subagent at), so the
doc cannot promise a mechanism that does not exist, and the test exercises that mechanism the
way the doc states it: a finding's id is its full text, so a word-for-word repeat of a closed
finding is the SAME id, and the SAME spot with different words is a DIFFERENT id. A test that
only compares identical text proves nothing about the id.
"""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.services.review.engine import parse
from ddflow.services.review.model import finding_digest

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "ddflow" / "drivers" / "review-contract.md"


def _digest(f) -> str:
    return finding_digest(f.severity, f.location, f.title, f.detail)


def test_the_contract_ships():
    assert CONTRACT.is_file(), "the review contract must ship"


def test_the_contract_states_the_stop_rule():
    text = CONTRACT.read_text()
    assert str(4) in text and "max_rounds" in text, "the stop rule must name round 4"
    for term in ("round", "finding", "refuted", "confirmed", "off contract"):
        assert re.search(term, text), f"the contract does not state the {term!r} rule"


def test_a_repeat_of_a_closed_finding_is_the_same_id():
    """A round that repeats a closed finding word-for-word is the SAME id, not a new one."""
    first = parse(
        "FINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input\nSTATUS: FINDINGS 1\n"  # noqa: E501
    )[0]
    repeat = parse(
        "STATUS: FINDINGS 1\nFINDING HIGH ddflow/core/nope.py:1\nreturns None on empty input\n"  # noqa: E501
    )[0]
    assert _digest(repeat[0]) == _digest(first[0]), "a repeated finding is not the same id"


def test_the_same_spot_with_different_words_is_a_different_id():
    """The id is the FULL text: the same location with different words is a DIFFERENT finding,
    so identity cannot be the location alone."""
    a = parse("FINDING HIGH ddflow/core/nope.py:1\nreturns None\nSTATUS: FINDINGS 1\n")[0]
    b = parse("FINDING HIGH ddflow/core/nope.py:1\nreturns the wrong type\nSTATUS: FINDINGS 1\n")[0]
    assert _digest(a[0]) != _digest(b[0]), "same location, different words, same id: id is not full text"


def test_an_off_contract_reply_is_flagged():
    """A round with no STATUS: block is off contract, never a clean review."""
    findings, on_contract = parse("FINDING MEDIUM ddflow/core/nope.py:2\nno status here\n")
    assert findings and not on_contract, "a reply with no STATUS: block was not flagged off contract"
