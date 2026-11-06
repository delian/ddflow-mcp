"""Reviewer independence as a preference (D-reviewer-independence-soft): family first, a
different model of the same family as a recorded tier `model`, the author's own model never."""

import pytest
from test_gates import item

from ddflow.core.model import fold
from ddflow.services import gates as G
from ddflow.services.gates import reviewers as GR


def _tier(log, cfg, author="claude-opus-5"):
    st = fold(log.read_all())
    return GR.independence_tier(st, cfg, "T1", author)


def _record(log, cfg, gd, model, gate="critic"):
    G.record(log, cfg, "T1", gate, "passed", evidence={"model": model}, gates=gd)


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


def test_default_is_model(cfg):
    assert cfg.agent.reviewer_independence == "model"


def test_a_different_family_is_tier_family(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "gemini-2.5-pro")
    assert _tier(log, cfg)[0] == "family"


def test_same_family_different_model_is_tier_model(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "claude-sonnet-5")
    tier, why = _tier(log, cfg)
    assert tier == "model" and "tier model" in why and "ddflow review --reviewer" in why
    assert G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")[0]


def test_the_authors_own_model_never_counts(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "claude-opus-5")
    assert _tier(log, cfg)[0] == "none"


def test_an_unrecognised_model_establishes_nothing(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "mystery-model-9")
    assert _tier(log, cfg)[0] == "none"


def test_a_bare_family_author_cannot_show_a_model_tier(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "claude-sonnet-5")
    assert _tier(log, cfg, author="anthropic")[0] == "none"


def test_family_level_refuses_the_fallback(log, cfg, gd):
    item(log)
    cfg.agent.reviewer_independence = "family"
    _record(log, cfg, gd, "claude-sonnet-5")
    assert _tier(log, cfg)[0] == "none"


def test_off_and_the_legacy_boolean(log, cfg, gd):
    item(log)
    _record(log, cfg, gd, "claude-opus-5")
    cfg.agent.reviewer_family_must_differ = False
    assert _tier(log, cfg)[0] == "off"
    cfg.agent.reviewer_family_must_differ = True
    cfg.agent.reviewer_independence = "off"
    assert _tier(log, cfg)[0] == "off"


def test_the_knob_declares_its_levels_and_the_strictest_fallback():
    from ddflow.config import KNOB_CHOICES, KNOB_STRICTEST

    key = "agent.reviewer_independence"
    assert KNOB_CHOICES[key] == ("family", "model", "off")
    assert KNOB_STRICTEST[key][0] == "family"


def test_tier_notes_are_neutral_and_once(log, cfg, gd):
    item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence={"model": "claude-sonnet-5", "independence_tier": "model"},
        gates=gd,
    )
    notes = GR.tier_notes(fold(log.read_all()).items["T1"], gd)
    assert notes == [
        "reviewed within the author's model family (critic by claude-sonnet-5; tier model). "
        "Hint: a reviewer of another family, if one is configured, is run with "
        "`ddflow review --reviewer <name>`."
    ]
    assert not any(w in notes[0].lower() for w in ("must", "warning", "refus"))


def test_family_level_outranks_the_legacy_boolean(cfg):
    cfg.agent.reviewer_independence = "family"
    cfg.agent.reviewer_family_must_differ = False
    assert GR.independence_level(cfg) == "family"


def test_a_declared_family_cannot_make_the_authors_own_model_count(cfg):
    assert GR.review_tier(cfg, "claude-opus-5", "claude-opus-5", "google") == "none"


def test_a_bare_family_name_is_no_reviewer_model(cfg):
    assert GR.review_tier(cfg, "claude-opus-5", "anthropic", "anthropic") == "none"


def test_a_bare_family_author_still_accepts_another_family(cfg):
    assert GR.review_tier(cfg, "anthropic", "gpt-5", "openai") == "family"


def _cross_family(gate, model, fam):
    """The evidence `ddflow review` records for one clean cross-family reviewer."""
    return {"reviewer": model, "model": model, "family": fam}


def test_an_unknown_author_with_a_cross_family_panel_is_independent(log, cfg, gd):
    """Bug B1a30dc3303: an author with no session model has no family to compare a
    reviewer with, so no SINGLE reviewer can be shown to differ from it. Reviewers from
    TWO different families (grok=xai, gemini=google) reviewed, though, and the author has
    exactly one family -- one of them is outside it whatever it is."""
    item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence=_cross_family("grok-4.7", "grok-4.7", "xai"),
        gates=gd,
    )
    G.record(
        log,
        cfg,
        "T1",
        "rubber_duck",
        "passed",
        evidence=_cross_family("gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "google"),
        gates=gd,
    )
    st = fold(log.read_all())
    tier, why = GR.independence_tier(st, cfg, "T1", "")
    assert tier == "family", why
    assert G.reviewer_independence(st, cfg, "T1", "")[0]


def test_an_unknown_author_with_a_single_family_panel_is_still_refused(log, cfg, gd):
    """The multi-family panel is the ONLY thing that can save an unknown author: one
    family still cannot be shown to differ from it."""
    item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence=_cross_family("grok-4.7", "grok-4.7", "xai"),
        gates=gd,
    )
    assert GR.independence_tier(fold(log.read_all()), cfg, "T1", "")[0] == "none"


def test_a_record_without_a_model_never_becomes_a_model_tier(log, cfg, gd):
    item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence={"family": "anthropic"},
        gates=gd,
        by="anthropic",
    )
    assert _tier(log, cfg)[0] == "none"


def test_a_cross_family_review_silences_the_tier_note(log, cfg, gd):
    item(log)
    for g, tier in (("critic", "model"), ("rubber_duck", "family")):
        G.record(
            log,
            cfg,
            "T1",
            g,
            "passed",
            evidence={"model": "m", "independence_tier": tier},
            gates=gd,
        )
    assert GR.tier_notes(fold(log.read_all()).items["T1"], gd) == []


def test_same_family_panel_is_not_independent(log, cfg, gd):
    cfg.agent.reviewer_independence = "family"
    item(log)
    G.record(
        log, cfg, "T1", "rubber_duck", "passed", evidence={"model": "claude-sonnet-5"}, gates=gd
    )
    G.record(
        log, cfg, "T1", "standards", "passed", evidence={"model": "claude-haiku-4-5"}, gates=gd
    )
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert not ok and "same as the author" in why
    G.record(log, cfg, "T1", "critic", "passed", evidence={"model": "gemini-2.5-pro"}, gates=gd)
    st = fold(log.read_all())
    ok, why = G.reviewer_independence(st, cfg, "T1", "claude-opus-5")
    assert ok and "google" in why


def test_a_review_that_did_not_run_neither_silences_nor_makes_the_note(log, cfg, gd):
    item(log)
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "passed",
        evidence={"model": "m", "independence_tier": "model"},
        gates=gd,
    )
    G.record(
        log,
        cfg,
        "T1",
        "rubber_duck",
        "unavailable",
        reason="down",
        evidence={"independence_tier": "family"},
        gates=gd,
    )
    assert len(GR.tier_notes(fold(log.read_all()).items["T1"], gd)) == 1
    G.record(
        log,
        cfg,
        "T1",
        "critic",
        "unavailable",
        reason="down",
        evidence={"independence_tier": "model"},
        gates=gd,
    )
    assert GR.tier_notes(fold(log.read_all()).items["T1"], gd) == []
