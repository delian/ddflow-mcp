"""WF-pins-regen-policy: the standing rule `pins_regenerate` (off by default).

An agent that regenerates a GENERATED pin file asks the operator about once per recipe and item;
the operator may switch a standing rule on in the local layer. The rule is registered in code, for
the verb `pins.regenerate` only, and stays inside every limit of the other standing rules: the
standing window, `policy_max_uses`, L1, never tier 3, recorded as `policy:pins_regenerate`. On top
of those: a recipe allowlist, recipe files inside `[lease].shared_globs`, one live work item the
agent holds, and a recipe definition that is the trusted checkout's own."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals.canon import Canon

VERB = "pins.regenerate"
RULE = "pins_regenerate"

RECIPES = """\
[[pin]]
name = "readme-knobs"
files = ["knobs.md"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "default-toml"
files = ["default.toml"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "golden-snapshots"
files = ["snaps/"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "result-schemas"
files = ["notes.md"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]

[[pin]]
name = "hand-written"
files = ["knobs.md"]
check = ["{python}", "-c", "pass"]
regen = ["{python}", "-c", "pass"]
"""


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    for args in (("add", ".ddflow/pins.toml"), ("commit", "-qm", "recipes")):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "t.py")
    code, _o, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="bot")
    assert code == 0, err
    return repo


def _cfg(repo, *, on=True, verbs=True, **over):
    cfg = Config.load(repo)
    cfg.lease.shared_globs = ["knobs.md", "default.toml", "snaps/**"]  # notes.md is hand-written
    if on:
        cfg.approvals.policy = [RULE]
    if verbs:
        cfg.approvals.standing_verbs = [*cfg.approvals.standing_verbs, VERB]
    for k, v in over.items():
        setattr(cfg.approvals, k, v)
    return cfg


def _canon(recipe="readme-knobs", agent="bot", **over):
    base = {"verb": VERB, "agent": agent, "item": recipe, "args": {"only": recipe}, "state": "s1"}
    base.update(over)
    return Canon(**base)


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _gate(repo, cfg, canon=None, *, agent="bot", **kw):
    return C.operator_gate(EventLog(repo, agent), cfg, canon or _canon(agent=agent), **kw)


def test_the_rule_answers_a_pins_regenerate_as_policy_and_is_on_the_record(proj):
    got = _gate(proj, _cfg(proj))
    assert got.ok and got.stamp["via"] == "policy"
    assert got.stamp["approved_by"] == f"policy:{RULE}" and got.stamp["level"] == 1
    row = asks_of(_state(proj))[got.ask_id]
    assert row["human"] is False and row["policy"] and row["used"] == 1
    assert row["answered_by"] == f"policy:{RULE}" and row["verb"] == VERB
    assert "work item: T1" in row["text"]
    code, out, _e = run_cli(proj, "approvals", "log")
    assert code == 0 and f"policy:{RULE}" in out
    _c, doctor, _e = run_cli(proj, "doctor")
    assert RULE in doctor, doctor


def test_the_rule_is_registered_in_code_for_the_one_verb():
    rule = PO.RULES[RULE]
    assert rule.verb == VERB


def test_off_by_default_a_person_is_asked(proj):
    assert Config.load(proj).approvals.policy == []
    got = _gate(proj, _cfg(proj, on=False))
    assert not got.ok and got.ask_id
    assert asks_of(_state(proj))[got.ask_id]["state"] == "pending"


def test_the_verb_must_be_in_standing_verbs(proj):
    got = _gate(proj, _cfg(proj, verbs=False))
    assert not got.ok and got.ask_id


@pytest.mark.parametrize("recipe", ["hand-written", "nosuch", ""])
def test_a_recipe_outside_the_allowlist_is_a_persons(proj, recipe):
    got = _gate(proj, _cfg(proj), _canon(recipe, args={"only": recipe}))
    assert not got.ok and got.ask_id
    assert asks_of(_state(proj))[got.ask_id]["state"] == "pending"


def test_the_allowlist_is_a_knob(proj):
    cfg = _cfg(proj, policy_pin_recipes=["hand-written"])
    assert not _gate(proj, cfg).ok  # readme-knobs left the list
    assert _gate(proj, cfg, _canon("hand-written", args={"only": "hand-written"})).ok


def test_an_allowlisted_recipe_whose_files_are_not_shared_is_a_persons(proj):
    got = _gate(proj, _cfg(proj), _canon("result-schemas", args={"only": "result-schemas"}))
    assert not got.ok and got.ask_id  # notes.md is outside [lease].shared_globs


def test_a_directory_recipe_inside_a_shared_glob_is_covered(proj):
    assert _gate(proj, _cfg(proj), _canon("golden-snapshots", args={"only": "golden-snapshots"})).ok


def test_one_use_per_recipe_and_item_then_a_person(proj):
    cfg = _cfg(proj, policy_max_uses=1)
    assert _gate(proj, cfg).ok
    again = _gate(proj, cfg)
    assert not again.ok and again.ask_id
    assert asks_of(_state(proj))[again.ask_id]["state"] == "pending"
    other = _gate(proj, cfg, _canon("default-toml", args={"only": "default-toml"}))
    assert other.ok  # the cap is per recipe: a different recipe has its own use


def test_a_second_work_item_has_its_own_use(proj):
    cfg = _cfg(proj)
    assert _gate(proj, cfg).ok
    run_cli(proj, "release", "T1", agent="bot")
    run_cli(proj, "task", "add", "T2", "--title", "t2", "--globs", "t2.py")
    assert run_cli(proj, "claim", "T2", "--no-worktree", agent="bot")[0] == 0
    got = _gate(proj, cfg)
    assert got.ok
    assert "work item: T2" in asks_of(_state(proj))[got.ask_id]["text"]


def test_past_the_standing_window_a_person_is_asked(proj, monkeypatch):
    cfg = _cfg(proj, standing_window_s=60)
    assert _gate(proj, cfg).ok  # opens the window
    real = PO.clock.now_utc
    monkeypatch.setattr(PO.clock, "now_utc", lambda: real() + timedelta(seconds=61))
    got = _gate(proj, cfg, _canon("default-toml", args={"only": "default-toml"}))
    assert not got.ok and got.ask_id
    monkeypatch.setattr(PO.clock, "now_utc", lambda: real() + timedelta(seconds=30))
    assert _gate(proj, cfg, _canon("golden-snapshots", args={"only": "golden-snapshots"})).ok


def test_an_agent_that_holds_no_work_item_is_a_persons(proj):
    got = _gate(proj, _cfg(proj), _canon(agent="mallory"), agent="mallory")
    assert not got.ok and got.ask_id  # T1 is bot's lease, not mallory's


def test_a_lease_that_lapsed_is_no_work_item(proj):
    cfg = _cfg(proj)
    run_cli(proj, "release", "T1", agent="bot")
    got = _gate(proj, cfg)
    assert not got.ok and got.ask_id


def test_an_explicit_request_stays_a_persons(proj):
    got = _gate(proj, _cfg(proj), approval_id="request")
    assert not got.ok and got.ask_id
    assert not asks_of(_state(proj))[got.ask_id]["policy"]


def test_a_person_only_verb_is_never_answered_by_the_rule(proj):
    cfg = _cfg(proj)
    for verb, item in (("config.reviewer_command", "x"), ("config.set", "approvals.policy")):
        cfg.approvals.standing_verbs = [*cfg.approvals.standing_verbs, verb]
        canon = Canon(verb=verb, agent="bot", item=item, state="s1")
        got = C.operator_gate(EventLog(proj, "bot"), cfg, canon)
        assert not got.ok and got.ask_id
        assert not asks_of(_state(proj))[got.ask_id]["policy"]


def test_the_rule_never_answers_above_l1(proj, monkeypatch):
    from ddflow.services.approvals import sites

    site = sites.BY_VERB[VERB]
    monkeypatch.setitem(sites.BY_VERB, VERB, site.__class__(**{**site.__dict__, "min_level": 2}))
    got = _gate(proj, _cfg(proj))
    assert not got.ok and got.ask_id


def test_the_rule_never_answers_a_tier_3_site(proj, monkeypatch):
    from ddflow.services.approvals import sites

    site = sites.BY_VERB[VERB]
    monkeypatch.setitem(sites.BY_VERB, VERB, site.__class__(**{**site.__dict__, "tier": sites.T3}))
    got = _gate(proj, _cfg(proj))
    assert not got.ok and got.ask_id


def _door(repo, monkeypatch, *, local=True):
    """`ddflow pins regenerate --only readme-knobs` as the agent `bot`, in this process: a
    subprocess from a linked worktree is refused by the foreign-import guard."""
    from ddflow.api import pin_files as PF
    from ddflow.services import install_info as I

    monkeypatch.setattr(I, "foreign_launch_root", lambda: None)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    if local:
        d = repo / ".ddflow" / "local"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.toml").write_text(
            f'[approvals]\npolicy = ["{RULE}"]\nstanding_verbs = ["{VERB}"]\n'
            '[lease]\nshared_globs = ["knobs.md"]\n'
        )
    return PF.pins_regenerate(repo, only="readme-knobs", agent="bot", start=repo)


def test_the_door_refuses_while_off_and_answers_itself_when_switched_on(proj, monkeypatch):
    """The falsifier: with the rule on, `pins regenerate --only R` as an agent needs no person."""
    off = _door(proj, monkeypatch, local=False)
    assert off.exit == 3, off  # off: refused, a request is filed for a person
    on = _door(proj, monkeypatch)
    assert on.exit == 0, on
    assert on.data["approval"]["via"] == "policy"
    assert on.data["approval"]["approved_by"] == f"policy:{RULE}"
    again = _door(proj, monkeypatch)
    assert again.exit == 3, again  # one use per recipe and item


def test_a_recipe_the_tree_changed_is_a_persons(proj, monkeypatch):
    """An agent that edits the recipe's regen command gets no standing answer for it."""
    path = proj / ".ddflow" / "pins.toml"
    path.write_text(RECIPES.replace('regen = ["{python}", "-c", "pass"]', 'regen = ["true"]', 1))
    got = _door(proj, monkeypatch)
    assert got.exit == 3, got


def test_a_pins_use_never_opens_or_extends_the_window_of_the_other_rules(proj):
    """The window is per scope: a regeneration anchors only the pins window, so it cannot keep
    `complete.force` and the other generic rules answering (a widening the reviewers named)."""
    cfg = _cfg(proj)
    assert PO.window_start(_state(proj), cfg) == 0.0
    assert _gate(proj, cfg).ok
    assert PO.window_start(_state(proj), cfg) == 0.0  # the generic window did not open
    assert PO.window_start(_state(proj), cfg, pins=True) > 0.0


def test_the_retry_threshold_never_answers_a_regeneration(proj):
    cfg = _cfg(proj, on=False, auto_approve_after_retries=1)
    first = _gate(proj, cfg)  # filed and left pending: the refusal count now meets the threshold
    again = _gate(proj, cfg)
    assert not first.ok and not again.ok
    assert not asks_of(_state(proj))[again.ask_id]["policy"]


def test_the_window_choice_is_not_offered_on_a_pins_ask(proj):
    from ddflow.services.approvals import harness as H

    cfg = _cfg(proj)
    assert VERB in cfg.approvals.standing_verbs  # in scope, and still no switch-on-four choice
    assert not H._generic(cfg, {"verb": VERB, "min_level": 1})
    assert H._generic(cfg, {"verb": "complete.force", "min_level": 1})


def test_a_use_with_no_recorded_work_item_counts_against_every_item(proj):
    """The cap fails closed: a drifted answer text can tighten it, never leave it unbounded."""
    cfg = _cfg(proj)
    got = _gate(proj, cfg)
    assert got.ok
    state = _state(proj)
    assert PO.used(state, RULE, _canon().subject(), "T1") == 1
    assert PO.used(state, RULE, _canon().subject(), "T2") == 0
    row = asks_of(state)[got.ask_id]
    row["text"] = "reworded, no marker"
    assert PO.used(state, RULE, _canon().subject(), "T2") == 1
    row["text"] = "x\n  work item: T1 (auto)\n  approved by standing rule: pins_regenerate"
    assert PO.used(state, RULE, _canon().subject(), "T1") == 1  # names no item that exists
