"""B7f815b7d88: an `agent.families` key is matched the way `agent.routers` keys are.

`family_for` lower-cased the model but not the key, so `families = {Claude = "anthropic"}`
never matched `claude-3-5-sonnet` and the operator's entry was silently ignored; and an
empty key matched every model, so one stray `"" = "openai"` made every reviewer OpenAI.
`router_set` already lower-cases its needle and skips an empty one; the two now agree.
"""

from __future__ import annotations

from ddflow.config import FAMILY_HINTS, family_for, router_set


def test_a_capitalised_key_matches_case_blind():
    assert family_for("claude-3-5-sonnet", {"Claude": "anthropic"}) == "anthropic"
    assert family_for("Claude-3-5-Sonnet", {"CLAUDE": "anthropic"}) == "anthropic"


def test_an_empty_key_matches_nothing():
    assert family_for("qwen2.5", {"": "openai"}) == ""
    assert family_for("qwen2.5", {"": "openai", "qwen": "alibaba"}) == "alibaba"


def test_the_shipped_hints_are_unchanged():
    for needle, fam in FAMILY_HINTS.items():
        assert family_for(f"x-{needle}-y") == fam


def test_families_and_routers_agree_on_the_match():
    assert router_set("My-HydraFusion", {"HydraFusion": ["openai"]}) == ["openai"]
    assert family_for("My-HydraFusion", {"HydraFusion": "openai"}) == "openai"
