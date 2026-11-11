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
from ddflow.services.review.model import blocking_findings, finding_digest

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "ddflow" / "drivers" / "review-contract.md"


def test_the_contract_ships():
    assert CONTRACT.is_file(), "the review contract must ship"


def test_the_contract_states_the_stop_rule():
    text = CONTRACT.read_text()
    assert str(4) in text and "max_rounds" in text, "the stop rule must name round 4"
    for term in ("round", "finding", "refuted", "confirmed", "off contract"):
        assert re.search(term, text), f"the contract does not state the {term!r} rule"


def test_a_repeated_closed_finding_is_flagged_by_its_digest():
    """The contract names one checker: a finding's full text is its id, so a round that
    repeats a finding already seen is the same id, and the floor filter keeps a repeated one
    standing instead of dropping it as a new nit."""
    closed = parse(
        "FINDING LOW ddflow/core/nope.py:1\nreturns None on empty input\nSTATUS: FINDINGS 1\n"
    )[0]
    digest = finding_digest(
        closed[0].severity, closed[0].location, closed[0].title, closed[0].detail
    )
    repeated = parse(
        "STATUS: FINDINGS 1\n"
        "FINDING LOW ddflow/core/nope.py:1\nreturns None on empty input\n"
    )[0]
    # The runner stamps the digest on each finding it keeps (the contract's "id"); the floor
    # filter then tells a NEW nit below the floor from a SAME words-again one.
    for f in repeated:
        f.digest = finding_digest(f.severity, f.location, f.title, f.detail)
    assert blocking_findings(repeated, converged=True, floor="HIGH", known=[]) == []
    assert blocking_findings(repeated, converged=True, floor="HIGH", known=[digest]), (
        "a repeated finding was dropped as a new nit instead of flagged"
    )


def test_an_off_contract_reply_is_flagged():
    """A round with no STATUS: block is off contract, never a clean review."""
    findings, on_contract = parse("FINDING MEDIUM ddflow/core/nope.py:2\nno status here\n")
    assert findings and not on_contract, "a reply with no STATUS: block was not flagged off contract"
