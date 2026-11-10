"""SW-hedge: straggler detection and capped hedging. Pure and read-only.

Pins the hedge contract a later dispatch caller relies on: a hedge goes to another family,
never the item's own; the spend cap stops the plan rather than fabricating a price; an
unverified spend or attempt price leaves the cap unchecked instead of guessed; and a
candidate with no other family produces no hedge.
"""

from ddflow.services.swarm import hedge as HG


def test_stragglers_are_items_at_or_past_the_threshold():
    assert HG.stragglers({"a": 120.0, "b": 59.0, "c": 60.0}, 60.0) == ("a", "c")


def test_candidates_merge_stragglers_and_hard_without_duplicates():
    assert HG.candidates({"a": 120.0, "b": 10.0}, 60.0, hard=("a", "d")) == ("a", "d")


def test_budget_is_a_ratio_of_fleet_spend_and_none_when_unverified():
    assert HG.budget(80.0, ratio=0.5) == 40.0
    assert HG.budget(None) is None


def test_a_hedge_dispatches_onto_the_first_other_family():
    plan = HG.plan(
        candidates=("a",),
        on={"a": ["deepseek"]},
        families=["qwen", "gemini"],
        attempt_usd=2.0,
        budget_usd=100.0,
    )
    assert [(h.item, h.family) for h in plan] == [("a", "qwen")]


def test_a_candidate_with_no_other_family_is_not_hedged():
    plan = HG.plan(
        candidates=("a",),
        on={"a": ["deepseek", "qwen"]},
        families=["deepseek", "qwen"],
        attempt_usd=2.0,
        budget_usd=100.0,
    )
    assert plan == ()


def test_the_spend_cap_stops_the_plan_not_fabricates_prices():
    # budget 10.0, each attempt 4.0: two fit, the third would exceed
    plan = HG.plan(
        candidates=("a", "b", "c"),
        on={"a": ["deepseek"], "b": ["deepseek"], "c": ["deepseek"]},
        families=["qwen"],
        attempt_usd=4.0,
        budget_usd=10.0,
    )
    assert [h.item for h in plan] == ["a", "b"]


def test_an_unverified_price_leaves_the_cap_unchecked_not_guessed():
    plan = HG.plan(
        candidates=("a", "b", "c"),
        on={"a": ["deepseek"], "b": ["deepseek"], "c": ["deepseek"]},
        families=["qwen"],
        attempt_usd=None,
        budget_usd=10.0,
    )
    assert [h.item for h in plan] == ["a", "b", "c"]


def test_the_reason_names_straggler_vs_hard():
    plan = HG.plan(
        candidates=("a", "b"),
        on={"a": ["deepseek"], "b": ["deepseek"]},
        families=["qwen"],
        hard=("b",),
        attempt_usd=1.0,
        budget_usd=100.0,
    )
    assert [(h.item, h.reason) for h in plan] == [("a", "straggler"), ("b", "hard")]
