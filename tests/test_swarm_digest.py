"""SW-digest: the swarm section of the operator digest. Pure and read-only.

These tests pin the contract a later P-operator-digest caller relies on: every figure is
either told from the arguments or named ``unavailable`` (``None``), never guessed. They also
guard the two compute terms the section owns -- ETA from the critical path, and hedges in
flight -- plus the spend estimate that must stay honest about unverified prices.
"""

from ddflow.services.swarm import digest as DG


def test_eta_sums_the_critical_path_and_names_the_method():
    secs, method = DG.eta_from_path(["a", "b", "c"], {"a": 30.0, "b": 60.0, "c": 30.0})
    assert secs == 120.0
    assert method == "critical-path"


def test_eta_is_unavailable_when_a_path_item_has_no_duration():
    secs, method = DG.eta_from_path(["a", "b"], {"a": 30.0})
    assert secs is None
    assert method == "critical-path"


def test_a_competing_attempt_is_a_hedge_and_a_lone_family_is_not():
    attempts = {
        "A": ["deepseek", "qwen"],  # two live families racing the same item
        "B": ["claude"],
    }
    rows = DG.hedges_from(attempts)
    assert {r.item: r.families for r in rows} == {"A": ("deepseek", "qwen")}


def test_spend_is_price_times_tokens_over_a_million():
    assert DG.spend_usd(1_000_000, 1_000_000, 1.0, 2.0) == 3.0


def test_a_local_route_is_free():
    assert DG.spend_usd(1_000_000, 1_000_000, None, None, local=True) == 0.0


def test_section_without_durations_is_unavailable_not_zero():
    # a caller that could not measure durations must not read as "done in 0s"
    s = DG.section(chain=["a", "b"], durations=None)
    assert s.eta_s is None


def test_section_with_an_empty_path_is_done():
    # no remaining work on the critical path: zero seconds left is a fact, not a guess
    s = DG.section(chain=[], durations={})
    assert s.eta_s == 0.0


def test_spend_is_unavailable_when_a_used_price_is_unverified():
    # in tokens are estimated but the in price is unknown: the figure cannot be told
    assert DG.spend_usd(1_000_000, 0, None, 2.0) is None
