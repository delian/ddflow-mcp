"""A router author is a SET of families (B-mixed-family-author).

GitHub Copilot CLI's HydraFusion is selected like a model, but routes each task across
models from several providers -- one drafts, another from a different family critiques,
a cascade escalates. An author named "hydrafusion" therefore has no single family:
`family_for('hydrafusion')` was '', so `complete --model hydrafusion` always refused,
and the obvious patch -- mapping it to ONE family in `[agent].families` -- would let a
reviewer from any of the OTHER families it drew on pass as independent of work that
family helped write. A reviewer is independent of a router only when its family is
outside the whole set, and a router whose set nobody has filled in (GitHub publishes no
fixed roster: research R-hydrafusion-families) is never assumed independent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services import gates as G


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


def _item(log):
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})


def _deepseek_review() -> dict:
    """The evidence shape `ddflow review` records for this project's LAN DeepSeek."""
    return {
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "reviewer": "lan-deepseek-v4.1-flash",
        "family": "deepseek",
        "status": "REVIEWED",
    }


def _check(log, cfg, gd, author: str, evidence: dict) -> tuple[bool, str]:
    _item(log)
    G.record(log, cfg, "T1", "critic", "passed", evidence=evidence, gates=gd)
    return G.reviewer_independence(fold(log.read_all()), cfg, "T1", author)


def test_a_router_author_is_independent_of_a_reviewer_outside_its_set(log, cfg, gd):
    cfg.agent.routers = {"hydrafusion": ["anthropic", "openai", "google"]}
    ok, why = _check(log, cfg, gd, "hydrafusion", _deepseek_review())
    assert ok, why
    assert "deepseek" in why, why


def test_a_reviewer_inside_the_set_is_refused_naming_the_overlap(log, cfg, gd):
    """The false pass this guards: mapping hydrafusion to one family (say openai) would
    have let a Claude reviewer through, though Claude may have written half the diff."""
    cfg.agent.routers = {"hydrafusion": ["Anthropic", "openai"]}
    ok, why = _check(log, cfg, gd, "hydrafusion", {"model": "claude-sonnet-5"})
    assert not ok, why
    assert "anthropic" in why and "hydrafusion" in why, why


def test_a_declared_reviewer_family_inside_the_set_is_refused(log, cfg, gd):
    cfg.agent.routers = {"hydrafusion": ["anthropic", "deepseek"]}
    ok, why = _check(log, cfg, gd, "hydrafusion", _deepseek_review())
    assert not ok and "deepseek" in why, why


def test_the_shipped_router_has_no_members_and_refuses_naming_the_knob(log, cfg, gd):
    """No published roster, so no shipped guess: the default must refuse, not pass."""
    assert "hydrafusion" in Config().agent.routers
    assert Config().agent.routers["hydrafusion"] == []
    ok, why = _check(log, cfg, gd, "hydrafusion", _deepseek_review())
    assert not ok, why
    # The ROUTER refusal, not the unknown-author one (which also names [agent].routers):
    # only the router branch says the set is empty.
    assert "a router in [agent].routers with no families listed" in why, why
    assert "[agent].families" not in why, why


def test_an_empty_router_is_matched_case_blind_and_still_refused(log, cfg, gd):
    """Substring and case-blind, like `[agent].families`: a served name such as
    `copilot/hydrafusion-preview` is still the router, not an unknown author."""
    cfg.agent.routers = {"HydraFusion": []}
    ok, why = _check(log, cfg, gd, "copilot/hydrafusion-preview", _deepseek_review())
    assert not ok, why
    assert "a router in [agent].routers with no families listed" in why, why


def test_a_configured_router_is_matched_case_blind(log, cfg, gd):
    cfg.agent.routers = {"HydraFusion": ["anthropic"]}
    ok, why = _check(log, cfg, gd, "copilot/hydrafusion-preview", _deepseek_review())
    assert ok and "(anthropic)" in why, why


def test_an_unknown_author_refusal_names_both_knobs(log, cfg, gd):
    """With the router entry removed, the author is merely unknown -- and the remedy must
    say a router belongs in [agent].routers, or the operator maps it to one family."""
    cfg.agent.routers = {}
    ok, why = _check(log, cfg, gd, "hydrafusion", _deepseek_review())
    assert not ok, why
    assert "[agent].families" in why and "[agent].routers" in why, why


def test_a_plain_single_family_author_is_unchanged(log, cfg, gd):
    cfg.agent.routers = {"hydrafusion": ["anthropic", "openai"]}
    ok, why = _check(log, cfg, gd, "claude-opus-5", _deepseek_review())
    assert ok and why == "critic was deepseek vs author anthropic", why


def test_a_single_family_author_with_a_same_family_reviewer_is_still_refused(log, cfg, gd):
    ok, why = _check(log, cfg, gd, "claude-opus-5", {"model": "claude-sonnet-5"})
    assert not ok and "same as the author" in why, why


def test_routers_load_from_toml_and_appear_in_config_explain(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        '[agent]\nrouters = { hydrafusion = ["anthropic", "openai"] }\n', "utf-8"
    )
    cfg = Config.load(tmp_path, env={})
    assert cfg.agent.routers == {"hydrafusion": ["anthropic", "openai"]}
    assert cfg.sources["agent.routers"] == "file"
    rows = {k: doc for k, _v, _s, doc in cfg.explain()}
    assert rows.get("agent.routers"), "the knob must be documented"


def test_routers_load_from_the_env_as_json():
    cfg = Config.load(None, env={"DDFLOW_AGENT_ROUTERS": '{"hydrafusion": ["openai"]}'})
    assert cfg.agent.routers == {"hydrafusion": ["openai"]}


@pytest.mark.parametrize(
    "bad",
    [
        {"agent": {"routers": {"hydrafusion": "openai"}}},
        {"agent": {"routers": {"hydrafusion": ["openai", 3]}}},
        {"agent": {"routers": ["hydrafusion"]}},
    ],
)
def test_a_malformed_router_is_refused_not_dropped(bad):
    """A string where a list belongs would iterate as letters ('o', 'p', ...): a set of
    nonsense families that matches no reviewer, and so passes every one."""
    with pytest.raises(ValueError, match=r"agent\.routers"):
        Config.check(bad)


@pytest.mark.parametrize(
    "routers",
    [
        {"hydra": ["anthropic"], "hydrafusion": ["openai", "anthropic"]},
        {"hydrafusion": ["anthropic"], "hydrafusion-pro": ["openai"]},
        {"hydrafusion-pro": ["openai"], "hydrafusion": ["anthropic"]},
    ],
)
def test_every_matching_router_contributes_to_the_set(log, cfg, gd, routers):
    """Bug B15af2d6420: first-match-wins on key order returned ONE matching entry, so a
    shorter needle listed first hid the longer one's members, and an openai reviewer
    passed as independent of a router that draws on openai. The set is the union of
    every matching entry, whatever the key order."""
    cfg.agent.routers = routers
    ok, why = _check(log, cfg, gd, "hydrafusion-pro", {"model": "gpt-6"})
    assert not ok and "openai" in why, why


def test_an_empty_matching_router_keeps_the_union_unknown(log, cfg, gd):
    """An entry with no members says "set unknown"; another match's members must not
    stand in for it as if they were the whole set."""
    cfg.agent.routers = {"hydra": ["anthropic"], "hydrafusion": []}
    ok, why = _check(log, cfg, gd, "hydrafusion", _deepseek_review())
    assert not ok and "no families listed" in why, why
