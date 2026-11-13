"""The supported gate (PAR-supported-gate): 'supported' means every required row answered.

A shipped descriptor is listed at both support tiers (the catalogue's two axes); a tier's
required rows are its catalogue rows (data), and a required row is ANSWERED at
verified/documented/none but only LISTED at unverified. These tests pin the tier rule: a
descriptor may not claim a tier while a required row is unverified, and the conformance
ratchet's parity gap set only shrinks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ddflow.services import harnessreg as HR  # noqa: E402
from ddflow.services.parity import answers as A  # noqa: E402
from ddflow.services.parity import catalog as C  # noqa: E402
from ddflow.services.parity import gate as G  # noqa: E402


def test_a_tier_is_a_catalog_axis_and_its_required_rows_are_its_rows():
    assert G.TIERS == (C.IN_AGENT, C.ORCHESTRATOR)
    for tier in G.TIERS:
        assert [r.feature for r in G.required_rows(tier)] == [
            r.feature for r in C.load().by_axis(tier)
        ]


def test_required_rows_are_data_not_code():
    # the required rows are the catalogue data file's axis sections, never a hard-coded list
    assert C.load().by_axis(C.IN_AGENT) and C.load().by_axis(C.ORCHESTRATOR)


def test_every_shipped_descriptor_is_listed_at_both_tiers():
    rows = C.load().rows
    for h in HR.load():
        got = A.answers(h)
        assert set(got) == {r.feature for r in rows}, h.id
        # the total derivation answers every required row, so nothing is unlisted
        assert G.unlisted(h) == ()


def test_unverified_counts_as_listed_not_answered():
    # a real, still-incomplete descriptor answers most rows `unverified`: the gate names
    # those rows as unanswered (listed, not answered), never as an answer against it
    h = HR.get("grok")
    got = A.answers(h)
    for f in G.unanswered(h):
        assert got[f] == "unverified"
    assert G.supported(h, C.IN_AGENT) is (G.unanswered(h, C.IN_AGENT) == ())


def test_a_required_row_answered_unverified_blocks_that_tier(monkeypatch):
    rows = {r.feature: "documented" for r in C.load().rows}
    rows["pre-tool-enforcement"] = "unverified"
    monkeypatch.setattr(A, "answers", lambda _h: rows)
    h = HR.get("grok")
    assert not G.supported(h, C.IN_AGENT)
    assert "pre-tool-enforcement" in G.unanswered(h, C.IN_AGENT)


def test_supported_requires_every_required_row_answered(monkeypatch):
    rows = {r.feature: "documented" for r in C.load().rows}
    rows["pre-tool-enforcement"] = "documented"
    monkeypatch.setattr(A, "answers", lambda _h: rows)
    h = HR.get("grok")
    assert G.supported(h, C.IN_AGENT)
    assert G.unanswered(h, C.IN_AGENT) == ()


def test_answered_names_every_status_but_unverified(monkeypatch):
    rows = {r.feature: "documented" for r in C.load().rows}
    rows["mcp-config"] = "verified"
    rows["session-start-brief"] = "none"
    rows["pre-tool-enforcement"] = "unverified"
    monkeypatch.setattr(A, "answers", lambda _h: rows)
    h = HR.get("grok")
    answered = G.answered(h, C.IN_AGENT)
    assert "mcp-config" in answered and "session-start-brief" in answered
    assert "pre-tool-enforcement" not in answered


def test_the_unsupported_tiers_are_named_per_agent():
    out = G.unsupported()
    for hid, tiers in out.items():
        assert tiers and hid in {h.id for h in HR.load()}


def test_the_parity_gap_set_only_shrinks():
    # a new catalog row answers `unverified` for every agent until each answers it, which
    # adds a parity gap per agent; the committed baseline only shrinks
    from harness_conformance import report as R

    now = set(R.parity_gaps())
    known = set(R.parity_baseline())
    assert not now - known, f"new parity gaps (answer the rows): {sorted(now - known)}"
    assert not known - now, f"stale parity gaps (regenerate gaps.json): {sorted(known - now)}"
