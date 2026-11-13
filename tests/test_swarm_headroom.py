"""SW-headroom: limit and budget headroom as a hard filter and a score term, read from the
layered ``[prices]`` table (TE-limit-aware-routing). No second price table."""

from __future__ import annotations

from dataclasses import replace

from ddflow.config import Config
from ddflow.config_sections import prices as PR
from ddflow.services import limit_routing as LR
from ddflow.services.swarm import headroom as H
from ddflow.services.swarm import ledger as L
from ddflow.services.swarm.score import Item, Lane, World, choose, evaluate, refusals
from ddflow.services.swarm.score_config import Weights


def _prices(**limit):
    return PR.PricesConfig(limit=dict(limit), reset={})


def _world():
    return World(ledger=L.Ledger({}))


def _lane(name, standing, model="m"):
    return Lane(name, "fam-" + name, model, standing=standing)


def test_standing_reuses_the_limit_routing_remaining_headroom():
    s = H.standing(_prices(m=10.0), "m", {"m": 4.0})
    assert s.remaining == LR.headroom(_prices(m=10.0), "m", {"m": 4.0}).remaining == 6.0
    assert s.limit == 10.0 and s.known


def test_missing_table_is_neutral_and_never_blocks():
    s = H.standing(_prices(), "m", {})
    assert not s.known
    assert H.refusals(s, floor_usd=0.0) == []
    assert H.term(s) == H.NEUTRAL
    assert refusals(Item("x"), _lane("a", s), _world(), Weights()) == []


def test_a_non_numeric_limit_is_unknown_neutral_not_blocking():
    s = H.standing(PR.PricesConfig(limit={"m": "unlimited"}, reset={}), "m", {})
    assert not s.known and H.refusals(s, floor_usd=0.0) == []


def test_exhausted_headroom_is_excluded_by_the_hard_filter():
    s = H.standing(_prices(m=5.0), "m", {"m": 5.0})
    assert H.refusals(s, floor_usd=0.0)
    d = evaluate(Item("x"), _lane("a", s), _world(), Weights())
    assert not d.ok and any("headroom" in r for r in d.reasons)
    assert choose(Item("x"), [_lane("a", s)], _world(), Weights()) is None


def test_subscription_limit_also_binds_a_lane():
    s = H.standing(_prices(m=100.0, sub=1.0), "m", {"sub": 1.0}, subscription="sub")
    assert H.refusals(s, floor_usd=0.0)


def test_estimated_cost_above_remaining_is_refused():
    s = H.standing(_prices(m=1.0), "m", {"m": 0.5})
    assert H.refusals(s, floor_usd=0.0, need_usd=0.4) == []
    assert H.refusals(s, floor_usd=0.0, need_usd=0.6)


def test_headroom_ordering_changes_the_pick():
    rich = H.standing(_prices(m=10.0), "m", {"m": 1.0})
    poor = H.standing(_prices(m=10.0), "m", {"m": 9.0})
    w = Weights(w_headroom=5.0)
    # same fit, same price: only headroom differs; the lane listed first is the poor one
    lanes = [_lane("a", poor), _lane("b", rich)]
    assert choose(Item("x"), lanes, _world(), w).lane == "b"
    assert choose(Item("x"), lanes, _world(), replace(w, w_headroom=0.0)).lane == "a"
    assert H.term(rich) > H.term(poor)


def test_weight_cannot_make_an_exhausted_lane_eligible():
    gone = H.standing(_prices(m=1.0), "m", {"m": 1.0})
    w = Weights(w_headroom=0.0)
    assert not evaluate(Item("x"), _lane("a", gone), _world(), w).ok
    w = Weights(w_headroom=1e9)
    assert not evaluate(Item("x"), _lane("a", gone), _world(), w).ok


def test_company_floor_cannot_be_loosened_by_the_caller():
    s = H.standing(_prices(m=10.0), "m", {"m": 9.5})  # 0.5 left
    assert H.effective_floor(2.0, 0.0) == 2.0  # a lower request is ignored
    assert H.effective_floor(2.0, 5.0) == 5.0  # a stricter one is honoured
    assert H.refusals(s, floor_usd=H.effective_floor(2.0, 0.0))
    assert not H.refusals(s, floor_usd=0.0)


def test_weights_and_floor_read_from_config():
    cfg = Config()
    cfg.swarm.w_headroom = 3.0
    cfg.swarm.headroom_floor_usd = 1.5
    w = Weights.from_config(cfg)
    assert (w.w_headroom, w.headroom_floor_usd) == (3.0, 1.5)
    assert Weights.from_config(Config()).w_headroom == Weights().w_headroom


def test_floor_from_weights_refuses_in_the_scheduler():
    s = H.standing(_prices(m=10.0), "m", {"m": 9.5})
    w = Weights(headroom_floor_usd=1.0)
    assert not evaluate(Item("x"), _lane("a", s), _world(), w).ok
    assert evaluate(Item("x"), _lane("a", s), _world(), Weights()).ok


def test_term_is_the_share_of_the_window_left():
    assert H.term(H.standing(_prices(m=10.0), "m", {})) == 1.0
    assert H.term(H.standing(_prices(m=10.0), "m", {"m": 5.0})) == 0.5
    assert H.term(H.standing(_prices(m=10.0), "m", {"m": 50.0})) == 0.0
