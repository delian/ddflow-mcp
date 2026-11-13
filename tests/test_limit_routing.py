"""TE-limit-aware-routing: ``[prices]`` is a layered spending-limit / reset table, and
``limit_routing.pick`` routes a mechanical item to the order's first model with headroom --
an exhausted model is skipped, and limit data that is UNAVAILABLE is not headroom."""

from __future__ import annotations

from ddflow.config_sections import prices as PR
from ddflow.services import limit_routing as LR


def _prices(**limit: object) -> PR.PricesConfig:
    return PR.PricesConfig(limit=dict(limit), reset={})


def test_the_table_routes_a_mechanical_item_to_the_first_enabled_model_with_headroom():
    table = _prices(cheap=10.0, strong=1.0)
    assert LR.pick(table, [("cheap", 0.01), ("strong", 0.6)]) == "cheap"


def test_an_exhausted_model_is_skipped_for_the_next_with_headroom():
    table = _prices(cheap=0.0, strong=10.0)
    assert LR.pick(table, [("cheap", 0.01), ("strong", 0.6)], {"cheap": 40.0}) == "strong"


def test_unavailable_limit_data_is_not_treated_as_headroom():
    """A model with no limit on record is UNKNOWN: ``headroom`` reports not known, so it is
    skipped like an exhausted one, never picked on faith."""
    table = _prices()
    assert LR.headroom(table, "strong").known is False
    assert LR.pick(table, [("strong", 0.6)]) is None


def test_remaining_headroom_subtracts_spent_from_the_limit():
    table = _prices(cheap=10.0)
    assert LR.headroom(table, "cheap", {"cheap": 10.0}).known is False
    assert LR.headroom(table, "cheap", {"cheap": 3.0}).remaining == 7.0


def test_a_non_numeric_limit_degrades_to_unknown_not_headroom():
    """A present-but-non-numeric limit (a typo'd string) is UNKNOWN, never headroom, and
    never raises at routing time (roborev 4652 finding)."""
    table = PR.PricesConfig(limit={"cheap": "unlimited"}, reset={})
    assert table.limit_of("cheap") is None
    assert LR.pick(table, [("cheap", 0.01)]) is None


def test_a_reset_defaults_to_1mo_when_the_model_names_none():
    table = PR.PricesConfig(limit={"cheap": 1.0}, reset={})
    assert table.reset_of("cheap") == "1mo"
    assert table.reset_of("other") == "1mo"
    assert PR.PricesConfig(limit={}, reset={"cheap": "1d"}).reset_of("cheap") == "1d"
