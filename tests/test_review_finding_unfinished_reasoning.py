"""A review that answers with reasoning, not a finding, is marked malformed (B-review-finding-is-unfinished-reasoning).

A reviewer can answer a chunk with a ``FINDING`` block whose location is not a citation but
the reviewer thinking aloud -- ``leaving.py:257? The line in _leave where _mine is called ...``.
Such a block was stored as a normal MEDIUM finding, failed the gate, and had to be triaged
by hand.

``parse`` now marks a block whose location is prose (not one token -- a bare path,
``path:line``, ``path:line:col``, ``path:line-line`` or ``path::symbol``) malformed, so it
is recorded (visible) but not counted against the gate: it neither blocks a round nor holds
a later delta open. Only the deterministic location signal is used; a finding that names a
real spot is never silently dropped, so its paragraph is not word-guessed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api.review.findings import _untriaged
from ddflow.services import review as RV

#: The finding reported on B-hy-stop-protocol: the location field is the reviewer
#: thinking aloud after a line number, not a citation.
PROSE_LOCATION = (
    "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257? The line in _leave where "
    "_mine is called ...\n"
    "We need cite line number and then look at what _mine does\n\n"
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


def test_parse_accepts_a_nested_pytest_symbol_as_a_citation():
    text = (
        "FINDING MEDIUM tests/test_parse.py::TestParse::test_last_finding\n"
        "the parser drops the last finding.\n\nSTATUS: FINDINGS 1"
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


def test_parse_keeps_a_citation_ending_in_a_question_mark():
    # A bare `path:line?` is uncertainty about the line, not prose: it stays a citation.
    text = (
        "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257?\n"
        "_leave dereferences _mine\n\nSTATUS: FINDINGS 1"
    )
    (f,) = RV.parse(text)[0]
    assert not f.malformed, "a citation with a trailing `?` is a spot, not prose"


def test_parse_keeps_a_semicolon_separated_citation():
    text = (
        "FINDING HIGH src/foo.py:12; src/bar.py:34\n"
        "the result is dropped before the write.\n\nSTATUS: FINDINGS 1"
    )
    (f,) = RV.parse(text)[0]
    assert not f.malformed, "a semicolon splits two spots, it is not prose"


def test_a_valid_finding_is_not_marked_malformed():
    (f,) = RV.parse(VALID)[0]
    assert not f.malformed


def test_reasoning_on_a_clean_citation_is_kept_not_dropped():
    # A paragraph is not word-guessed: a finding that names a real spot stays a finding
    # even when its paragraph is meta-talk (fail-closed).
    text = (
        "FINDING MEDIUM ddflow/api/lifecycle/leaving.py:257\n"
        "We need to cite a line number and then decide.\n\nSTATUS: FINDINGS 1"
    )
    (f,) = RV.parse(text)[0]
    assert not f.malformed


def test_a_malformed_finding_is_not_counted_against_the_gate():
    (f,) = RV.parse(PROSE_LOCATION)[0]
    assert RV.blocking_findings([f], converged=False, floor="HIGH") == []


def test_a_malformed_finding_does_not_hold_a_later_delta_open():
    kept = [{"digest": "deadbeef", "severity": "MEDIUM", "malformed": "location is prose"}]
    assert _untriaged(kept, gone=set(), carried={}, verdicts={}) == 0


def test_merge_rerun_carries_the_malformed_flag_across_a_chunk_rerun():
    prior = {
        "reviewer": "b",
        "diff_sha": "deadbeef",
        "max_chunk_chars": 4000,
        "reviewed": [1, 2],
        "chunks_total": 2,
        "coverage": "2/2 chunk(s) reviewed",
        "chunk_findings": [
            {
                "chunk": 2,
                "digest": "abc123",
                "severity": "MEDIUM",
                "title": "x.py:1",
                "location": "x.py:1",
                "detail": "the spot",
                "reviewer": "a",
                "family": "xai",
                "malformed": "location is prose, not a path:line or path:symbol",
            }
        ],
        "unreviewed": [],
    }
    res = RV.ReviewResult(
        "b", "grok-4.7", "google", status=RV.REVIEWED, requested=[2], reviewed=[2]
    )
    res.diff_sha = "deadbeef"
    res.max_chunk_chars = 4000
    assert RV.merge_rerun(prior, res) == ""
    kept_f = next(f for f in res.findings if f.digest == "abc123")
    assert kept_f.malformed, "a malformed finding stays malformed across a --chunk re-review"


def test_a_valid_finding_still_blocks():
    (f,) = RV.parse(VALID)[0]
    assert RV.blocking_findings([f], converged=False, floor="HIGH") == [f]
