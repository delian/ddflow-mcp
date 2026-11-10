"""A review that answers with reasoning, not a finding, is marked malformed (B-review-finding-is-unfinished-reasoning).

A reviewer can answer a chunk with a ``FINDING`` block whose location is not a citation but
the reviewer thinking aloud -- ``leaving.py:257? The line in _leave where _mine is called ...``
-- or whose paragraph is unfinished reasoning that states no defect (``We need cite line
number``). Such a block was stored as a normal MEDIUM finding, failed the gate, and had to
be triaged by hand.

``parse`` now marks such a block malformed, so it is recorded (visible) but not counted
against the gate: it neither blocks a round nor holds a later delta open.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import review as RV

#: The finding reported on B-hy-stop-protocol: the location field is the reviewer
#: thinking aloud after a line number, not a citation.
PROSE_LOCATION = (
    "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257? The line in _leave where "
    "_mine is called ...\n"
    "We need cite line number and then look at what _mine does\n\n"
    "STATUS: FINDINGS 1"
)

#: Unfinished reasoning on a clean citation: the reviewer never stated a defect.
UNFINISHED = (
    "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257\n"
    "We should think about whether this is correct and then write it up later\n\n"
    "STATUS: FINDINGS 1"
)

VALID = (
    "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257\n"
    "_leave calls _mine before the flag is set.\n\n"
    "STATUS: FINDINGS 1"
)


def test_parse_marks_a_finding_whose_location_is_prose_as_malformed():
    (f,) = RV.parse(PROSE_LOCATION)[0]
    assert f.malformed, "a prose location is reasoning, not a citation"


def test_parse_marks_unfinished_reasoning_on_a_clean_location_as_malformed():
    (f,) = RV.parse(UNFINISHED)[0]
    assert f.malformed, "a paragraph that states no defect and never ends is reasoning"


def test_parse_accepts_a_nested_pytest_symbol_as_a_citation():
    text = (
        "FINDING MEDIUM tests/test_parse.py::TestParse::test_last_finding\n"
        "the parser drops the last finding\n\nSTATUS: FINDINGS 1"
    )
    (f,) = RV.parse(text)[0]
    assert not f.malformed, "a nested `path::Class::func` is a citation, not prose"


def test_parse_accepts_a_line_column_citation():
    text = (
        "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257:12\n"
        "_leave dereferences _mine before _mine is assigned.\n\nSTATUS: FINDINGS 1"
    )
    (f,) = RV.parse(text)[0]
    assert not f.malformed, "a `path:line:col` is a citation, not prose"


def test_a_valid_finding_is_not_marked_malformed():
    (f,) = RV.parse(VALID)[0]
    assert not f.malformed


def test_a_malformed_finding_is_not_counted_against_the_gate():
    (f,) = RV.parse(PROSE_LOCATION)[0]
    assert RV.blocking_findings([f], converged=False, floor="HIGH") == []


def test_a_valid_finding_still_blocks():
    (f,) = RV.parse(VALID)[0]
    assert RV.blocking_findings([f], converged=False, floor="HIGH") == [f]
